"""Validate closed input, apply inventory anchors and seal immutable model inputs."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from jsonschema import ValidationError

from src.operations.contracts import FEATURES, Conflict, at, calendar_values, days, parsed, validate
from src.operations.db import audit, digest, enqueue, insert, one, rows


def ingest(conn: object, scope: str, job: dict) -> dict:
    """Use only complete, available records and atomically queue a sealed forecast."""
    delivery = one(conn, "SELECT * FROM app.delivery WHERE id=%s", (job["resource_id"],))
    body = one(conn, "SELECT body FROM app.artifact_payload WHERE artifact_id=%s", (delivery["artifact_id"],))["body"]
    state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
    start, end = delivery["week_start"], delivery["week_end_exclusive"]
    items = {str(r["id"]): r for r in rows(conn, "SELECT * FROM app.item")}
    patients = [f["feature_key"] for f in FEATURES if f["kind"] == "patient"]
    try:
        if delivery["kind"] == "weekly":
            validate("WeeklyDeliveryRequest", body)
        if not body["is_complete"] or start.weekday() != 0:
            raise Conflict("INCOMPLETE_OR_NOT_MONDAY")
        if delivery["source_closed_at"] > delivery["source_available_at"] or delivery["source_available_at"] > state["simulation_at"]:
            raise Conflict("FUTURE_AVAILABILITY")
        if parsed(body["source_closed_at"]) < at(end) - timedelta(hours=1):
            raise Conflict("WEEK_NOT_CLOSED")
        expected = {(key, str(day)) for key in items for day in days(start, end)}
        actual = {(r["item_id"], r["day"]) for r in body["usage"]}
        if len(actual) != len(body["usage"]) or actual != expected:
            raise Conflict("INCOMPLETE_OR_DUPLICATE_USAGE")
        expected_patients = {(key, str(day)) for key in patients for day in days(start, end)}
        actual_patients = {(r["feature_key"], r["day"]) for r in body["patients"]}
        if len(actual_patients) != len(body["patients"]) or actual_patients != expected_patients:
            raise Conflict("INCOMPLETE_OR_DUPLICATE_PATIENTS")
        for row in body["usage"]:
            if row["unit"] != items[row["item_id"]]["base_unit"] or parsed(row["available_at"]) > delivery["source_available_at"]:
                raise Conflict("UNIT_OR_AVAILABILITY_MISMATCH")
            if row["observation"] == "observed" and (row["quantity"] is None or Decimal(row["quantity"]) < 0):
                raise Conflict("INVALID_QUANTITY")
        for row in body["patients"]:
            if parsed(row["available_at"]) > delivery["source_available_at"]:
                raise Conflict("PATIENT_FUTURE_AVAILABILITY")
        stock = body["inventory"]
        if stock is None and state["profile"]["name"] != "cheonan_records_v1":
            raise Conflict("INVENTORY_REQUIRED")
        if stock is not None:
            if parsed(stock["as_of"]) > delivery["source_closed_at"] or set(r["item_id"] for r in stock["lines"]) != set(items) or len(stock["lines"]) != len(items):
                raise Conflict("INCOMPLETE_OR_FUTURE_STOCK")
            for row in stock["lines"]:
                if row["unit"] != items[row["item_id"]]["base_unit"] or Decimal(row["unavailable"]) + Decimal(row["allocated"]) > Decimal(row["on_hand"]):
                    raise Conflict("INVALID_STOCK")
    except (Conflict, ValueError, ValidationError) as error:
        insert(conn, "validation_issue", scope, delivery_id=delivery["id"], code="INPUT_REJECTED", field_path="$", severity="error", message=str(error)[:1000])
        conn.execute("UPDATE app.delivery SET status='rejected',validation_completed_at=now() WHERE id=%s", (delivery["id"],))
        conn.execute("UPDATE app.scope SET status='failed',blocking_code='INPUT_REJECTED' WHERE id=%s", (scope,))
        return {"delivery_id": str(delivery["id"]), "failure": "INPUT_REJECTED"}
    for row in body["usage"]:
        insert(conn, "usage_day", scope, delivery_id=delivery["id"], **row)
    for row in body["patients"]:
        insert(conn, "patient_day", scope, delivery_id=delivery["id"], **row)
    if stock is not None:
        anchor = insert(conn, "inventory_anchor", scope, delivery_id=delivery["id"], as_of=stock["as_of"], source_version=stock["source_version"], status="applied")
        for line in stock["lines"]:
            insert(conn, "inventory_anchor_line", scope, anchor_id=anchor, **line)
            current = conn.execute("SELECT * FROM app.inventory_state WHERE item_id=%s", (line["item_id"],)).fetchone()
            if current is None:
                insert(conn, "inventory_state", scope, item_id=line["item_id"], anchor_id=anchor, as_of=stock["as_of"], on_hand=line["on_hand"], unavailable=line["unavailable"], allocated=line["allocated"])
            else:
                previous = one(conn, "SELECT as_of FROM app.inventory_anchor WHERE id=%s", (current["anchor_id"],))
                if parsed(stock["as_of"]) >= previous["as_of"]:
                    # The authoritative anchor already includes all events at or before its time.
                    later = one(conn, "SELECT coalesce(sum(delta_on_hand),0) AS delta FROM app.inventory_event WHERE item_id=%s AND occurred_at>%s", (line["item_id"], stock["as_of"]))["delta"]
                    conn.execute("UPDATE app.inventory_state SET anchor_id=%s,on_hand=%s,unavailable=%s,allocated=%s,version=version+1,as_of=%s WHERE id=%s",
                                 (anchor, Decimal(line["on_hand"])+later, line["unavailable"], line["allocated"], state["simulation_at"], current["id"]))
    for line in body["open_orders"]:
        line = dict(line)
        matched = conn.execute("SELECT l.id FROM app.purchase_order_line l JOIN app.purchase_order o ON o.id=l.order_id WHERE o.supplier_id=%s AND (o.order_key::text=%s OR o.supplier_order_ref=%s) AND l.item_id=%s", (line["supplier_id"], line["source_order_key"], line["source_order_key"], line["item_id"])).fetchone()
        insert(conn, "external_open_order", scope, delivery_id=delivery["id"], matched_order_line_id=matched["id"] if matched else None, **line)
    if delivery["supersedes_id"]:
        changed = conn.execute("UPDATE app.delivery SET status='superseded' WHERE id=%s AND status='validated'", (delivery["supersedes_id"],))
        if changed.rowcount != 1:
            raise Conflict("DELIVERY_REVISION_CONFLICT")
    conn.execute("UPDATE app.delivery SET status='validated',validation_completed_at=now() WHERE id=%s", (delivery["id"],))
    enqueue(conn, scope, "evaluate", str(delivery["id"]), str(job["id"]))
    if delivery["supersedes_id"]:
        impacted = rows(conn, "SELECT s.* FROM app.input_snapshot s JOIN app.snapshot_source x ON x.snapshot_id=s.id WHERE x.delivery_id=%s", (delivery["supersedes_id"],))
        needs_resubmission = []
        for old_snapshot in impacted:
            conn.execute("UPDATE app.forecast_run SET validity='input_revised' WHERE snapshot_id=%s", (old_snapshot["id"],))
            conn.execute("UPDATE app.proposal SET status='superseded' WHERE status IN ('pending','blocked') AND forecast_id IN (SELECT id FROM app.forecast_run WHERE snapshot_id=%s)", (old_snapshot["id"],))
            historical = state["simulation_at"] >= at(old_snapshot["forecast_origin"])
            resolved = old_snapshot["delivery_id"] == delivery["supersedes_id"]
            status = "resolved" if resolved else "historical_only" if historical else "waiting_resubmission"
            insert(conn, "delivery_impact", scope, correction_delivery_id=delivery["id"], affected_delivery_id=old_snapshot["delivery_id"], snapshot_id=old_snapshot["id"], status=status,
                   resolution_delivery_id=delivery["id"] if resolved else None, reason_code="INPUT_REVISION")
            if status == "waiting_resubmission":
                needs_resubmission.append(str(old_snapshot["delivery_id"]))
        # A corrective latest-week submission resolves prior invalidated latest-origin requests.
        conn.execute("UPDATE app.delivery_impact SET status='resolved',resolution_delivery_id=%s WHERE affected_delivery_id=%s AND status='waiting_resubmission'", (delivery["id"], delivery["supersedes_id"]))
        if end < state["next_week_start"] or state["simulation_at"] >= at(end):
            conn.execute("UPDATE app.scope SET status=%s,blocking_code=%s,active_week_start=NULL,active_job_id=NULL WHERE id=%s", ("failed" if needs_resubmission else "ready", "CORRECTION_RESUBMISSION_REQUIRED" if needs_resubmission else None, scope))
            return {"delivery_id": str(delivery["id"]), "required_resubmission_delivery_ids": needs_resubmission}
    if delivery["kind"] == "bootstrap":
        conn.execute("UPDATE app.scope SET status='ready',active_job_id=NULL WHERE id=%s", (scope,))
        return {"delivery_id": str(delivery["id"])}
    knowledge = max(delivery["source_available_at"], state["simulation_at"])
    snapshot = insert(conn, "input_snapshot", scope, delivery_id=delivery["id"], model_version_id=delivery["model_version_id"],
                      forecast_origin=end, cutoff_at=delivery["source_closed_at"], knowledge_as_of=knowledge, status="building", manifest_hash=digest(body))
    targets = rows(conn, """SELECT DISTINCT ON (u.item_id,u.day) u.* FROM app.usage_day u
        JOIN app.delivery d ON d.id=u.delivery_id WHERE d.status='validated' AND u.day>=%s AND u.day<%s
        AND u.available_at<=%s AND d.source_available_at<=%s
        ORDER BY u.item_id,u.day,d.source_revision DESC,d.received_at DESC""", (end-timedelta(days=112), end, knowledge, knowledge))
    if len(targets) != len(items)*112:
        raise Conflict("INSUFFICIENT_HISTORY")
    sources = set()
    for row in targets:
        sources.add(row["delivery_id"])
        insert(conn, "snapshot_target", scope, snapshot_id=snapshot, model_version_id=delivery["model_version_id"], item_id=row["item_id"], day=row["day"], quantity=row["quantity"], observation=row["observation"])
    for source in sources:
        insert(conn, "snapshot_source", scope, snapshot_id=snapshot, delivery_id=source, start_day=end-timedelta(days=112), end_day_exclusive=end)
    patient_rows = rows(conn, """SELECT DISTINCT ON (p.feature_key,p.day) p.* FROM app.patient_day p
        JOIN app.delivery d ON d.id=p.delivery_id WHERE d.status='validated' AND p.day>=%s AND p.day<%s
        AND p.available_at<=%s ORDER BY p.feature_key,p.day,d.source_revision DESC,d.received_at DESC""", (end-timedelta(days=28), end, knowledge))
    if len(patient_rows) != 69*28:
        raise Conflict("INSUFFICIENT_PATIENT_HISTORY")
    for row in patient_rows:
        insert(conn, "snapshot_feature", scope, snapshot_id=snapshot, model_version_id=delivery["model_version_id"], kind="patient", feature_key=row["feature_key"], day=row["day"], value=row["value"], observation=row["observation"])
    for day in days(end-timedelta(days=112), end+timedelta(days=14)):
        for key, value in calendar_values(day, knowledge).items():
            insert(conn, "snapshot_feature", scope, snapshot_id=snapshot, model_version_id=delivery["model_version_id"], kind="known", feature_key=key, day=day, value=value, observation="observed", announced_at=delivery["source_available_at"])
    conn.execute("UPDATE app.input_snapshot SET status='sealed',sealed_at=now() WHERE id=%s", (snapshot,))
    forecast_job = enqueue(conn, scope, "forecast", snapshot, str(job["id"]))
    forecast = insert(conn, "forecast_run", scope, snapshot_id=snapshot, model_version_id=delivery["model_version_id"], job_id=forecast_job, status="pending")
    audit(conn, scope, "snapshot.sealed", snapshot, {"forecast_id": forecast}, job=str(job["id"]))
    return {"delivery_id": str(delivery["id"]), "snapshot_id": snapshot, "forecast_id": forecast, "child_job_ids": [forecast_job]}
