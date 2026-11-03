"""Order recommendations, explicit decisions, supplier reconciliation and stock receipts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
import os
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from src.operations.contracts import Conflict, SEOUL, at, parsed
from src.operations.db import audit, digest, enqueue, insert, one, plain, rows, transaction


def arrival(decision_at: datetime, policy: dict) -> datetime:
    """Evaluate calendar-day lead time using the policy's local order cutoff."""
    local = decision_at.astimezone(SEOUL)
    cutoff = policy["calendar_rule"]["cutoff_local"]
    base = local.date() + timedelta(days=int(local.strftime("%H:%M") >= cutoff))
    return at(base+timedelta(days=policy["lead_days"]))


def proposal(conn: object, scope: str, job: dict) -> dict:
    """Calculate a versioned full-scope proposal from a current actual forecast."""
    state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
    run = one(conn, "SELECT f.*,s.forecast_origin,s.delivery_id FROM app.forecast_run f JOIN app.input_snapshot s ON s.id=f.snapshot_id WHERE f.id=%s", (job["resource_id"],))
    if run["status"] != "succeeded" or run["validity"] != "current":
        raise Conflict("INVALID_FORECAST")
    now = state["simulation_at"]
    origin = run["forecast_origin"]
    if state["profile"]["name"] == "cheonan_records_v1":
        # No stock or purchasing policy has been provided. Never invent a recommendation.
        conn.execute("UPDATE app.scope SET status='ready',version=version+CASE WHEN last_completed_week_start=active_week_start THEN 0 ELSE 1 END,next_week_start=%s,last_completed_week_start=active_week_start,active_week_start=NULL,active_job_id=NULL,blocking_code=NULL WHERE id=%s", (origin, scope))
        return {"forecast_id": str(run["id"])}
    next_demand = at(origin)
    if now >= next_demand:
        raise Conflict("APPROVAL_WINDOW_CLOSED")
    previous = conn.execute("SELECT * FROM app.proposal WHERE forecast_id=%s ORDER BY version DESC LIMIT 1", (run["id"],)).fetchone()
    if previous and previous["status"] in ("pending", "blocked"):
        conn.execute("UPDATE app.proposal SET status='superseded' WHERE id=%s", (previous["id"],))
    lines = []
    all_blocks = set()
    for item in rows(conn, "SELECT * FROM app.item ORDER BY code"):
        policy = one(conn, "SELECT * FROM app.ordering_policy WHERE item_id=%s AND valid_from<=%s ORDER BY version DESC LIMIT 1", (item["id"], now))
        inventory = one(conn, "SELECT * FROM app.inventory_state WHERE item_id=%s", (item["id"],))
        first_arrival = arrival(now, policy)
        protection = arrival(now+timedelta(days=7), policy).date()
        blocks = []
        if protection > origin+timedelta(days=14):
            blocks.append("PROTECTION_EXCEEDS_HORIZON")
        available = inventory["on_hand"]-inventory["unavailable"]-inventory["allocated"]
        predictions = rows(conn, "SELECT * FROM app.forecast_value WHERE forecast_id=%s AND item_id=%s ORDER BY target_day", (run["id"], item["id"]))
        forecast_units = sum((p["prediction"] for p in predictions if p["target_day"] < protection), Decimal(0))
        internal = rows(conn, "SELECT l.*,o.status FROM app.purchase_order_line l JOIN app.purchase_order o ON o.id=l.order_id WHERE l.item_id=%s AND o.released_at IS NULL AND o.status NOT IN ('voided','rejected')", (item["id"],))
        unknown = [p for p in internal if p["status"] in ("submission_unknown", "sending")]
        if unknown:
            blocks.append("UNKNOWN_ORDER")
        incoming = sum((p["ordered_units"]-p["received_units"]-p["cancelled_units"] for p in internal if p["status"] == "accepted" and p["expected_at"] <= at(protection)), Decimal(0))
        reserved = sum((p["ordered_units"]-p["received_units"]-p["cancelled_units"] for p in internal if p["status"] != "accepted"), Decimal(0))
        external = rows(conn, "SELECT * FROM app.external_open_order WHERE delivery_id=%s AND item_id=%s AND matched_order_line_id IS NULL", (run["delivery_id"], item["id"]))
        if any(p["status"] == "unknown" for p in external):
            blocks.append("UNKNOWN_EXTERNAL_ORDER")
        incoming += sum((p["remaining_units"] for p in external if p["status"] == "open" and p["expected_at"] <= at(protection)), Decimal(0))
        # Project each day, admitting supply only on its actual arrival day.
        projected = available
        for prediction in predictions:
            day = prediction["target_day"]
            if at(day) >= first_arrival:
                break
            projected += sum((p["ordered_units"]-p["received_units"]-p["cancelled_units"] for p in internal if p["status"] == "accepted" and p["expected_at"].astimezone(SEOUL).date() == day), Decimal(0))
            projected += sum((p["remaining_units"] for p in external if p["status"] == "open" and p["expected_at"].astimezone(SEOUL).date() == day), Decimal(0))
            projected -= prediction["prediction"]
            if projected < 0:
                blocks.append("PREARRIVAL_STOCKOUT")
                break
        required = max(Decimal(0), forecast_units+policy["safety_units"]-available-incoming-reserved)
        packs = max(policy["minimum_packs"], int((required/policy["pack_size"]).to_integral_value(rounding=ROUND_CEILING))) if required else 0
        all_blocks.update(blocks)
        lines.append({"item_id": item["id"], "policy_id": policy["id"], "inventory_version": inventory["version"],
                      "protection_end": protection, "forecast_units": forecast_units, "safety_units": policy["safety_units"],
                      "available_units": available, "eligible_incoming": incoming, "reserved_order_units": reserved,
                      "required_units": required, "packs": packs, "order_units": policy["pack_size"]*packs,
                      "unit": item["base_unit"], "basis": {"expected_at": first_arrival.isoformat(), "policy_hash": policy["policy_hash"],
                      "supply_hash": digest(sorted(plain(internal), key=lambda r: r["id"]))},
                      "blocking_codes": sorted(set(blocks))})
    version = previous["version"]+1 if previous else 1
    content_hash = digest({"forecast_id": run["id"], "version": version, "decision_as_of": now, "lines": lines})
    proposed = insert(conn, "proposal", scope, forecast_id=run["id"], version=version,
                      supersedes_id=previous["id"] if previous else None, status="blocked" if all_blocks else "pending",
                      content_hash=content_hash, decision_as_of=now, expires_at=next_demand, next_demand_at=next_demand,
                      blocking_codes=sorted(all_blocks), job_id=job["id"])
    for line in lines:
        insert(conn, "proposal_line", scope, proposal_id=proposed, **line)
    audit(conn, scope, "proposal.calculated", proposed, {"blocking_codes": sorted(all_blocks)}, job=str(job["id"]))
    if state["status"] == "advancing":
        conn.execute("UPDATE app.scope SET status='ready',version=version+CASE WHEN last_completed_week_start=active_week_start THEN 0 ELSE 1 END,next_week_start=%s,last_completed_week_start=active_week_start,active_week_start=NULL,active_job_id=NULL,blocking_code=NULL WHERE id=%s", (origin, scope))
    return {"forecast_id": str(run["id"]), "proposal_id": proposed}


def decide(conn: object, scope: str, proposal_id: str, principal: str, body: dict) -> dict:
    """Commit one explicit Yes/No and only positive approved order lines atomically."""
    proposed = one(conn, "SELECT * FROM app.proposal WHERE id=%s", (proposal_id,))
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    if proposed["status"] not in ("pending", "blocked") or proposed["version"] != body["proposal_version"] or proposed["content_hash"] != body["content_hash"]:
        raise Conflict("PROPOSAL_VERSION_CONFLICT")
    if proposed["expires_at"] <= now:
        raise Conflict("PROPOSAL_EXPIRED")
    lines = rows(conn, "SELECT l.*,p.supplier_id,p.policy_hash,p.valid_from FROM app.proposal_line l JOIN app.ordering_policy p ON p.id=l.policy_id WHERE proposal_id=%s ORDER BY l.id", (proposal_id,))
    if body["decision"] == "approve":
        if proposed["status"] != "pending":
            raise Conflict("PROPOSAL_BLOCKED")
        if one(conn, "SELECT validity FROM app.forecast_run WHERE id=%s", (proposed["forecast_id"],))["validity"] != "current":
            raise Conflict("INPUT_REVISED")
        if conn.execute("SELECT 1 FROM app.delivery d JOIN app.snapshot_source s ON s.delivery_id=d.supersedes_id JOIN app.forecast_run f ON f.snapshot_id=s.snapshot_id WHERE f.id=%s AND d.status IN ('received','validating') LIMIT 1", (proposed["forecast_id"],)).fetchone():
            raise Conflict("CORRECTION_PENDING")
        for line in lines:
            current = one(conn, "SELECT version FROM app.inventory_state WHERE item_id=%s", (line["item_id"],))
            policy = one(conn, "SELECT id FROM app.ordering_policy WHERE item_id=%s AND valid_from<=%s ORDER BY version DESC LIMIT 1", (line["item_id"], now))
            if current["version"] != line["inventory_version"] or policy["id"] != line["policy_id"]:
                raise Conflict("RECALCULATION_REQUIRED")
            incoming = rows(conn, "SELECT l.*,o.status FROM app.purchase_order_line l JOIN app.purchase_order o ON o.id=l.order_id WHERE l.item_id=%s AND o.released_at IS NULL AND o.status NOT IN ('voided','rejected')", (line["item_id"],))
            if digest(sorted(plain(incoming), key=lambda r: r["id"])) != line["basis"]["supply_hash"]:
                raise Conflict("RECALCULATION_REQUIRED")
    decision = insert(conn, "decision", scope, proposal_id=proposal_id, proposal_version=proposed["version"],
                      content_hash=proposed["content_hash"], choice=body["decision"], principal_id=principal,
                      reason=body.get("reason"), decided_at=now)
    order_ids = []
    if body["decision"] == "approve":
        for supplier in sorted({str(l["supplier_id"]) for l in lines if l["order_units"] > 0}):
            supplier_lines = [l for l in lines if str(l["supplier_id"]) == supplier and l["order_units"] > 0]
            order = insert(conn, "purchase_order", scope, decision_id=decision, supplier_id=supplier,
                           order_key=str(uuid4()), content_hash=digest(supplier_lines), status="pending_send")
            order_ids.append(order)
            for line in supplier_lines:
                order_line = insert(conn, "purchase_order_line", scope, order_id=order, proposal_line_id=line["id"],
                                    item_id=line["item_id"], packs=line["packs"], ordered_units=line["order_units"],
                                    expected_at=line["basis"]["expected_at"], unit=line["unit"])
                insert(conn, "order_reservation", scope, order_line_id=order_line, item_id=line["item_id"], remaining_units=line["order_units"], status="reserved")
            enqueue(conn, scope, "submit", order)
    conn.execute("UPDATE app.proposal SET status=%s WHERE id=%s", ("approved" if body["decision"] == "approve" else "rejected", proposal_id))
    audit(conn, scope, "decision.recorded", decision, {"choice": body["decision"], "order_ids": order_ids}, principal)
    return {"id": decision, "proposal_id": proposal_id, "proposal_version": proposed["version"],
            "content_hash": proposed["content_hash"], "decision": body["decision"], "decided_by": principal,
            "decided_at": now, "order_ids": order_ids, "dispatch_status": "queued" if order_ids else "not_required"}


def send(scope: str, job: dict, token: str) -> dict:
    """Submit with a durable key; every subsequent attempt looks up an uncertain result."""
    from src.operations.ml_fence import assert_fence

    with transaction(scope) as conn:
        assert_fence(conn, str(job["id"]), token)
        order = one(conn, "SELECT * FROM app.purchase_order WHERE id=%s", (job["resource_id"],))
        if order["status"] in ("accepted", "voided", "rejected", "blocked"):
            return {"order_ids": [str(order["id"])]}
        prior = conn.execute("SELECT 1 FROM app.order_attempt WHERE order_id=%s AND kind='submit' LIMIT 1", (order["id"],)).fetchone()
        lookup = bool(prior) or job["stage"] == "reconcile"
        if not lookup:
            proposal_row = one(conn, "SELECT p.* FROM app.proposal p JOIN app.decision d ON d.proposal_id=p.id WHERE d.id=%s", (order["decision_id"],))
            now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
            validity = one(conn, "SELECT validity FROM app.forecast_run WHERE id=%s", (proposal_row["forecast_id"],))["validity"]
            pending_correction = conn.execute("SELECT 1 FROM app.delivery d JOIN app.snapshot_source s ON s.delivery_id=d.supersedes_id JOIN app.forecast_run f ON f.snapshot_id=s.snapshot_id WHERE f.id=%s AND d.status IN ('received','validating') LIMIT 1", (proposal_row["forecast_id"],)).fetchone()
            policy_changed = conn.execute("SELECT 1 FROM app.purchase_order_line l JOIN app.proposal_line p ON p.id=l.proposal_line_id WHERE l.order_id=%s AND p.policy_id<>(SELECT id FROM app.ordering_policy WHERE item_id=l.item_id AND valid_from<=%s ORDER BY version DESC LIMIT 1) LIMIT 1", (order["id"], now)).fetchone()
            if now >= proposal_row["expires_at"] or validity != "current" or pending_correction or policy_changed:
                conn.execute("UPDATE app.purchase_order SET status='blocked' WHERE id=%s", (order["id"],))
                return {"order_ids": [str(order["id"])]}
            mismatched = conn.execute("SELECT 1 FROM app.purchase_order_line l JOIN app.proposal_line p ON p.id=l.proposal_line_id JOIN app.inventory_state i ON i.item_id=l.item_id WHERE l.order_id=%s AND p.inventory_version<>i.version LIMIT 1", (order["id"],)).fetchone()
            if mismatched:
                conn.execute("UPDATE app.purchase_order SET status='blocked' WHERE id=%s", (order["id"],))
                return {"order_ids": [str(order["id"])]}
        attempt = one(conn, "SELECT id FROM app.job_attempt WHERE job_id=%s AND fencing_token=%s", (job["id"], token))
        order_attempt = insert(conn, "order_attempt", scope, order_id=order["id"], job_attempt_id=attempt["id"], kind="lookup" if lookup else "submit", request_hash=order["content_hash"], started_at=datetime.now().astimezone(), outcome="in_flight")
        conn.execute("UPDATE app.purchase_order SET status=%s,submit_fence=%s,submit_lease_until=now()+interval '180 seconds' WHERE id=%s", ("submission_unknown" if lookup else "sending", token, order["id"]))
        body = {"scope_id": scope, "content_hash": order["content_hash"], "accepted_at": plain(one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]),
                "lines": plain(rows(conn, "SELECT id,item_id,ordered_units,unit,expected_at FROM app.purchase_order_line WHERE order_id=%s ORDER BY id", (order["id"],)))}
    outcome, data = "unknown", {}
    try:
        with httpx.Client(timeout=10) as client:
            url = f"{os.environ['SUPPLIER_URL']}/orders/{order['order_key']}"
            response = client.get(url) if lookup else client.put(url, json=body)
        if response.status_code == 200:
            data = response.json()
            if data["content_hash"] != order["content_hash"]:
                raise Conflict("SUPPLIER_HASH_MISMATCH")
            outcome = "accepted"
        elif response.status_code == 404:
            outcome = "not_found"
    except (httpx.HTTPError, Conflict):
        outcome = "unknown"
    with transaction(scope) as conn:
        assert_fence(conn, str(job["id"]), token)
        conn.execute("UPDATE app.order_attempt SET finished_at=now(),outcome=%s,supplier_ref=%s WHERE id=%s", (outcome, data.get("supplier_ref"), order_attempt))
        if outcome == "accepted":
            conn.execute("UPDATE app.purchase_order SET status='accepted',supplier_order_ref=%s,accepted_at=%s WHERE id=%s AND submit_fence=%s", (data["supplier_ref"], data["accepted_at"], order["id"], token))
            conn.execute("UPDATE app.order_reservation SET status='confirmed' WHERE order_line_id IN (SELECT id FROM app.purchase_order_line WHERE order_id=%s)", (order["id"],))
        else:
            conn.execute("UPDATE app.purchase_order SET status='submission_unknown' WHERE id=%s AND submit_fence=%s", (order["id"], token))
        audit(conn, scope, "order.supplier_result", str(order["id"]), {"outcome": outcome}, job=str(job["id"]))
    return {"order_ids": [str(order["id"])]}


def reverse(conn: object, scope: str, receipt_id: str, body: dict) -> tuple[str, str]:
    """Create a single full reversal while retaining the original applied receipt."""
    original = one(conn, "SELECT * FROM app.receipt WHERE id=%s", (receipt_id,))
    if original["status"] != "applied" or original["reverses_receipt_id"] is not None:
        raise Conflict("RECEIPT_NOT_REVERSIBLE")
    if conn.execute("SELECT 1 FROM app.receipt WHERE reverses_receipt_id=%s", (receipt_id,)).fetchone():
        raise Conflict("RECEIPT_ALREADY_REVERSED")
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    if not original["occurred_at"] <= parsed(body["occurred_at"]) <= now:
        raise Conflict("INVALID_REVERSAL_TIME")
    lines = rows(conn, "SELECT * FROM app.receipt_line WHERE receipt_id=%s", (receipt_id,))
    for line in lines:
        stock = one(conn, "SELECT s.*,a.as_of AS anchor_time FROM app.inventory_state s JOIN app.inventory_anchor a ON a.id=s.anchor_id JOIN app.purchase_order_line l ON l.item_id=s.item_id WHERE l.id=%s", (line["order_line_id"],))
        if original["occurred_at"] <= stock["anchor_time"]:
            raise Conflict("ANCHOR_RECONCILIATION_REQUIRED")
        if stock["on_hand"]-stock["unavailable"]-stock["allocated"] < line["quantity"]:
            raise Conflict("REVERSAL_NEGATIVE_STOCK")
    resource = str(uuid4())
    job = enqueue(conn, scope, "receipt", resource)
    insert(conn, "receipt", scope, id=resource, order_id=original["order_id"], source_receipt_key=body["source_receipt_key"],
           occurred_at=body["occurred_at"], status="received", reverses_receipt_id=receipt_id, body_hash=digest(body), job_id=job)
    for line in lines:
        insert(conn, "receipt_line", scope, receipt_id=resource, order_id=original["order_id"], order_line_id=line["order_line_id"], quantity=line["quantity"], unit=line["unit"])
    return resource, job


def receive(conn: object, scope: str, order_id: str, body: dict) -> tuple[str, str]:
    """Accept a keyed receipt and queue the shared atomic stock-application handler."""
    order = one(conn, "SELECT * FROM app.purchase_order WHERE id=%s", (order_id,))
    old = conn.execute("SELECT * FROM app.receipt WHERE order_id=%s AND source_receipt_key=%s", (order_id, body["source_receipt_key"])).fetchone()
    if old:
        if old["body_hash"] != digest(body):
            raise Conflict("RECEIPT_KEY_CONFLICT")
        return str(old["id"]), str(old["job_id"])
    validate_receipt(conn, scope, order, body)
    receipt = str(uuid4())
    job = enqueue(conn, scope, "receipt", receipt)
    insert(conn, "receipt", scope, id=receipt, order_id=order_id, source_receipt_key=body["source_receipt_key"], occurred_at=body["occurred_at"], status="received", body_hash=digest(body), job_id=job)
    for line in body["lines"]:
        insert(conn, "receipt_line", scope, receipt_id=receipt, order_id=order_id, **line)
    return receipt, job


def validate_receipt(conn: object, scope: str, order: dict, body: dict) -> None:
    if order["status"] != "accepted":
        raise Conflict("ORDER_NOT_ACCEPTED")
    occurred = parsed(body["occurred_at"])
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    if occurred > now or occurred < order["accepted_at"]:
        raise Conflict("INVALID_RECEIPT_TIME")
    seen = set()
    for line in body["lines"]:
        current = one(conn, "SELECT * FROM app.purchase_order_line WHERE id=%s AND order_id=%s", (line["order_line_id"], order["id"]))
        quantity = Decimal(line["quantity"])
        if line["order_line_id"] in seen or not quantity.is_finite() or quantity <= 0 or quantity > current["ordered_units"]-current["received_units"]-current["cancelled_units"] or line["unit"] != current["unit"]:
            raise Conflict("INVALID_RECEIPT_LINE")
        seen.add(line["order_line_id"])


def apply_receipt(conn: object, scope: str, job: dict) -> dict:
    """Revalidate remaining quantities, then update order and stock exactly once."""
    receipt = one(conn, "SELECT * FROM app.receipt WHERE id=%s", (job["resource_id"],))
    if receipt["status"] == "applied":
        return {"receipt_id": str(receipt["id"])}
    order = one(conn, "SELECT * FROM app.purchase_order WHERE id=%s", (receipt["order_id"],))
    lines = rows(conn, "SELECT * FROM app.receipt_line WHERE receipt_id=%s", (receipt["id"],))
    if receipt["reverses_receipt_id"]:
        return apply_reversal(conn, scope, job, receipt, lines)
    validate_receipt(conn, scope, order, {"occurred_at": receipt["occurred_at"], "lines": [{"order_line_id": str(l["order_line_id"]), "quantity": str(l["quantity"]), "unit": l["unit"]} for l in lines]})
    for line in lines:
        current = one(conn, "SELECT * FROM app.purchase_order_line WHERE id=%s", (line["order_line_id"],))
        stock = one(conn, "SELECT s.*,a.as_of AS anchor_time FROM app.inventory_state s JOIN app.inventory_anchor a ON a.id=s.anchor_id WHERE s.item_id=%s", (current["item_id"],))
        effect = receipt["occurred_at"] > stock["anchor_time"]
        version = stock["version"]+int(effect)
        insert(conn, "inventory_event", scope, item_id=current["item_id"], source_key=f"receipt:{line['id']}",
               stock_effect=effect, event_kind="receipt", occurred_at=receipt["occurred_at"], delta_on_hand=line["quantity"], receipt_line_id=line["id"], affected_version=version)
        if effect:
            conn.execute("UPDATE app.inventory_state SET on_hand=on_hand+%s,version=%s,as_of=greatest(as_of,%s) WHERE id=%s", (line["quantity"], version, receipt["occurred_at"], stock["id"]))
        conn.execute("UPDATE app.purchase_order_line SET received_units=received_units+%s WHERE id=%s", (line["quantity"], current["id"]))
        conn.execute("UPDATE app.order_reservation SET remaining_units=remaining_units-%s WHERE order_line_id=%s", (line["quantity"], current["id"]))
    remaining = one(conn, "SELECT sum(ordered_units-received_units-cancelled_units) AS n FROM app.purchase_order_line WHERE order_id=%s", (order["id"],))["n"]
    conn.execute("UPDATE app.purchase_order SET receipt_status=%s WHERE id=%s", ("received" if remaining == 0 else "partially_received", order["id"]))
    conn.execute("UPDATE app.receipt SET status='applied' WHERE id=%s", (receipt["id"],))
    audit(conn, scope, "receipt.applied", str(receipt["id"]), {"order_id": str(order["id"])}, job=str(job["id"]))
    return {"receipt_id": str(receipt["id"])}


def apply_reversal(conn: object, scope: str, job: dict, receipt: dict, lines: list[dict]) -> dict:
    """Recheck stock/anchor at application, then reverse order totals and stock atomically."""
    original = one(conn, "SELECT * FROM app.receipt WHERE id=%s AND status='applied' AND reverses_receipt_id IS NULL", (receipt["reverses_receipt_id"],))
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    if not original["occurred_at"] <= receipt["occurred_at"] <= now:
        raise Conflict("INVALID_REVERSAL_TIME")
    for line in lines:
        order_line = one(conn, "SELECT * FROM app.purchase_order_line WHERE id=%s", (line["order_line_id"],))
        stock = one(conn, "SELECT s.*,a.as_of AS anchor_time FROM app.inventory_state s JOIN app.inventory_anchor a ON a.id=s.anchor_id WHERE s.item_id=%s", (order_line["item_id"],))
        if original["occurred_at"] <= stock["anchor_time"]:
            raise Conflict("ANCHOR_RECONCILIATION_REQUIRED")
        if stock["on_hand"]-stock["unavailable"]-stock["allocated"] < line["quantity"] or order_line["received_units"] < line["quantity"]:
            raise Conflict("REVERSAL_NEGATIVE_STOCK")
        original_event = one(conn, "SELECT e.* FROM app.inventory_event e JOIN app.receipt_line l ON l.id=e.receipt_line_id WHERE l.receipt_id=%s AND l.order_line_id=%s", (original["id"], line["order_line_id"]))
        insert(conn, "inventory_event", scope, item_id=order_line["item_id"], source_key=f"reverse:{line['id']}", event_kind="reversal",
               occurred_at=receipt["occurred_at"], delta_on_hand=-line["quantity"], receipt_line_id=line["id"], reverses_event_id=original_event["id"], affected_version=stock["version"]+1)
        conn.execute("UPDATE app.inventory_state SET on_hand=on_hand-%s,version=version+1 WHERE id=%s", (line["quantity"], stock["id"]))
        conn.execute("UPDATE app.purchase_order_line SET received_units=received_units-%s WHERE id=%s", (line["quantity"], line["order_line_id"]))
        conn.execute("UPDATE app.order_reservation SET remaining_units=remaining_units+%s WHERE order_line_id=%s", (line["quantity"], line["order_line_id"]))
    received = one(conn, "SELECT sum(received_units) AS n FROM app.purchase_order_line WHERE order_id=%s", (receipt["order_id"],))["n"]
    conn.execute("UPDATE app.purchase_order SET receipt_status=%s WHERE id=%s", ("not_received" if received == 0 else "partially_received", receipt["order_id"]))
    conn.execute("UPDATE app.receipt SET status='applied' WHERE id=%s", (receipt["id"],))
    audit(conn, scope, "receipt.reversed", str(receipt["id"]), {"original_receipt_id": str(original["id"])}, job=str(job["id"]))
    return {"receipt_id": str(receipt["id"])}


def release(conn: object, scope: str, order_id: str, principal: str, body: dict) -> dict:
    """Release only definitely unsent blocked orders or a proven final rejection."""
    order = one(conn, "SELECT * FROM app.purchase_order WHERE id=%s", (order_id,))
    if order["status"] != body["expected_status"] or order["content_hash"] != body["content_hash"] or order["released_at"]:
        raise Conflict("ORDER_VERSION_CONFLICT")
    attempts = rows(conn, "SELECT * FROM app.order_attempt WHERE order_id=%s ORDER BY started_at", (order_id,))
    unsent = order["status"] == "blocked" and not any(a["kind"] == "submit" for a in attempts)
    rejected = order["status"] == "rejected" and attempts and attempts[-1]["outcome"] == "rejected"
    if not (unsent or rejected):
        raise Conflict("ORDER_RELEASE_UNSAFE")
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    conn.execute("UPDATE app.purchase_order SET status=%s,released_at=%s,released_by=%s,release_reason=%s,submit_fence=NULL,submit_lease_until=NULL WHERE id=%s", ("voided" if unsent else "rejected", now, principal, body["reason"], order_id))
    conn.execute("UPDATE app.order_reservation SET status='released',remaining_units=0 WHERE order_line_id IN (SELECT id FROM app.purchase_order_line WHERE order_id=%s)", (order_id,))
    conn.execute("UPDATE app.outbox SET status='held' WHERE job_id IN (SELECT id FROM app.job WHERE resource_id=%s AND stage IN ('submit','reconcile'))", (order_id,))
    conn.execute("UPDATE app.job SET status='blocked',error_code='ORDER_RELEASED' WHERE resource_id=%s AND stage IN ('submit','reconcile') AND status IN ('queued','retry_wait')", (order_id,))
    audit(conn, scope, "order.released", order_id, {"reason": body["reason"]}, principal)
    return one(conn, "SELECT * FROM app.purchase_order WHERE id=%s", (order_id,))
