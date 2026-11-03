"""Deterministic synthetic history and weekly simulation without patient identifiers."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import os
from uuid import uuid4

from psycopg.types.json import Jsonb

from src.operations.contracts import FEATURES, Conflict, at, days
from src.operations.db import audit, digest, enqueue, insert, one, plain, rows


def patient_counts(seed: int, day: date) -> dict[str, int]:
    """Generate internally consistent independent marginal counts, never real people."""
    count = 90 + int(hashlib.sha256(f"{seed}:{day}".encode()).hexdigest()[:4], 16) % 21
    result = {f["feature_key"]: 0 for f in FEATURES if f["kind"] == "patient"}
    result["total__ALL"] = count
    for prefix in ("sex__", "encounter__", "age__", "main_kcd__", "sub_kcd__"):
        keys = [k for k in result if k.startswith(prefix)]
        for i in range(count):
            result[keys[(i + day.toordinal() + seed) % len(keys)]] += 1
    return result


def requested(seed: int, item: dict, day: date) -> Decimal:
    token = int(hashlib.sha256(f"{seed}:{item['code']}:{day}".encode()).hexdigest()[:8], 16)
    return Decimal(8 + token % 6)


def store_delivery(conn: object, scope: str, body: dict, principal: str, kind: str = "weekly") -> tuple[str, str]:
    """Atomically persist canonical input, metadata and the ingestion command."""
    encoded = json.dumps(plain(body), sort_keys=True).encode()
    artifact = insert(conn, "artifact", scope, kind="delivery", storage_key=f"deliveries/{uuid4()}.json",
                      sha256=digest(body), byte_count=len(encoded), media_type="application/json", access_class="input")
    insert(conn, "artifact_payload", scope, artifact_id=artifact, body=plain(body))
    record = insert(conn, "delivery", scope, kind=kind, week_start=body["week_start"],
                    week_end_exclusive=body["week_end_exclusive"], source_revision=body["source_revision"],
                    supersedes_id=body["supersedes_delivery_id"], source_closed_at=body["source_closed_at"],
                    source_available_at=body["source_available_at"], received_at=body["source_available_at"],
                    model_version_id=body["model_version_id"], artifact_id=artifact, body_hash=digest(body),
                    submitted_by=principal, status="received")
    job = enqueue(conn, scope, "ingest", record)
    audit(conn, scope, "delivery.received", record, {"kind": kind}, principal)
    return record, job


def make_delivery(conn: object, scope: str, start: date, end: date, bootstrap: bool) -> dict:
    state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
    if state["profile"]["name"] == "cheonan_records_v1":
        from src.operations.records import make_recorded_delivery
        return make_recorded_delivery(conn, scope, start, end)
    items = rows(conn, "SELECT * FROM app.item ORDER BY code")
    close = at(end) - timedelta(minutes=60)
    available = at(end) - timedelta(minutes=30)
    usage = []
    patients = []
    for day in days(start, end):
        for item in items:
            if bootstrap:
                quantity = requested(state["seed"], item, day)
            else:
                quantity = one(conn, "SELECT fulfilled_units FROM app.demand_outcome WHERE item_id=%s AND day=%s", (item["id"], day))["fulfilled_units"]
            usage.append({"item_id": str(item["id"]), "day": str(day), "quantity": str(quantity),
                          "observation": "observed", "unit": item["base_unit"], "available_at": available.isoformat()})
        patients.extend({"feature_key": key, "day": str(day), "value": value, "observation": "observed",
                         "available_at": available.isoformat()} for key, value in patient_counts(state["seed"], day).items())
    inventory = rows(conn, "SELECT s.*,i.base_unit FROM app.inventory_state s JOIN app.item i ON i.id=s.item_id")
    return {"schema_version": 1, "week_start": str(start), "week_end_exclusive": str(end),
            "source_revision": 1, "supersedes_delivery_id": None, "source_closed_at": close.isoformat(),
            "source_available_at": available.isoformat(), "is_complete": True,
            "model_version_id": str(state["model_version_id"]), "usage": usage, "patients": patients,
            "inventory": {"as_of": close.isoformat(), "source_version": f"{end}:1", "lines": [
                {"item_id": str(i["item_id"]), "on_hand": str(i["on_hand"]), "unavailable": str(i["unavailable"]),
                 "allocated": str(i["allocated"]), "unit": i["base_unit"]} for i in inventory]}, "open_orders": []}


def bootstrap(conn: object, scope: str, job: dict) -> dict:
    """Register actual checkpoint contract, synthetic cohort and complete initial history."""
    state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
    profile = state["profile"]
    recorded = profile["name"] == "cheonan_records_v1"
    catalogue = None
    if recorded:
        from src.operations.records import load_records
        catalogue = load_records()
    if not os.getenv("ML_IMAGE_DIGEST", "").startswith("sha256:"):
        raise Conflict("MODEL_RUNTIME_NOT_REGISTERED")
    principal = str(one(conn, "SELECT principal_id FROM app.membership WHERE role='test_operator' LIMIT 1")["principal_id"])
    supplier = insert(conn, "supplier", scope, code="SYNTHETIC", name="테스트 공급처", adapter="synthetic") if not recorded else None
    artifact = insert(conn, "artifact", scope, kind="model", storage_key="model/model.safetensors",
                      sha256="a7592b0a8432baee54483254e5647856911ce69e09d09a9bb65904b2d98f17da",
                      byte_count=1322898824, media_type="application/octet-stream", access_class="model")
    drug_count, material_count = (20, 10) if profile["name"] in ("timesfm30_v1", "cheonan_records_v1") else (3, 2)
    model = insert(conn, "model_version", scope, name="timesfm-3.0-fixed-cpu-v1",
                   checkpoint_revision="43046b85ec22d584a13f8098c2ed39c889e129c2", checkpoint_artifact_id=artifact,
                   code_commit=os.environ["APP_CODE_COMMIT"], image_digest=os.environ["ML_IMAGE_DIGEST"],
                   backend="cpu", expected_drugs=drug_count, expected_materials=material_count,
                   manifest={"synthetic": not recorded, "recorded_dataset": {k: catalogue[k] for k in ("dataset_id", "input_sha256", "availability")} if catalogue else None, "fixed_weights": True, "quantile": "median_nonnegative",
                   "source_sha256": os.environ["APP_SOURCE_SHA256"], "uncommitted_code": os.environ["APP_CODE_DIRTY"] == "1",
                   "official_code_commit": "e51928e27119cb17bebc005be2696b75e0a9e688", "license": "timesfm-non-commercial-license-v1.0"}, status="validated")
    conn.execute("UPDATE app.scope SET model_version_id=%s WHERE id=%s", (model, scope))
    for feature in FEATURES:
        insert(conn, "model_feature", scope, model_version_id=model, **feature)
    # Anchors must reference a delivery. The initial body is assembled before state exists.
    items = []
    for kind, count in (("drug", drug_count), ("material", material_count)):
        for ordinal in range(count):
            meta = next((m for m in catalogue["items"] if m["channel"] == f"{kind}_{ordinal+1:02d}"), None) if catalogue else None
            item = insert(conn, "item", scope, code=meta["channel"] if meta else f"{kind}-{ordinal+1:02d}", name=meta["name"] if meta else f"테스트 {'약품' if kind=='drug' else '물품'} {ordinal+1}",
                          kind=kind, base_unit=meta["unit"] if meta else "개", target_semantics="drug_record_units" if kind == "drug" else "material_issue_units")
            items.append(item)
            insert(conn, "model_item", scope, model_version_id=model, item_id=item, cohort=kind, ordinal=ordinal)
            if recorded:
                continue
            policy = {"item_id": item, "supplier_id": supplier, "version": 1, "lead_days": 1,
                      "review_days": 7, "pack_size": "10", "minimum_packs": 1, "safety_units": "20",
                      "valid_from": state["simulation_at"], "calendar_rule": {"mode": "calendar_days",
                      "business_weekdays": list(range(1, 8)), "cutoff_local": "23:00", "timezone": "Asia/Seoul", "arrival_local": "00:00"}}
            insert(conn, "ordering_policy", scope, **policy, policy_hash=digest(policy))
    end = state["next_week_start"]
    body = make_delivery(conn, scope, end - timedelta(days=profile["history_days"]), end, True)
    if not recorded:
        body["inventory"]["lines"] = [{"item_id": item, "on_hand": "140", "unavailable": "0", "allocated": "0", "unit": "개"} for item in items]
    delivery, ingest = store_delivery(conn, scope, body, principal, "bootstrap")
    conn.execute("UPDATE app.scope SET active_job_id=%s WHERE id=%s", (ingest, scope))
    return {"delivery_id": delivery, "child_job_ids": [ingest]}


def simulate(conn: object, scope: str, job: dict) -> dict:
    """Advance one closed week exactly once, receiving due orders before consumption."""
    from src.operations.business import apply_receipt, receive

    state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
    if state["profile"]["name"] == "cheonan_records_v1":
        from src.operations.records import advance_recorded_week
        return advance_recorded_week(conn, scope, job)
    start = state["active_week_start"]
    if start != state["next_week_start"]:
        raise Conflict("STALE_WEEK")
    end = start + timedelta(days=7)
    principal = str(one(conn, "SELECT principal_id FROM app.membership WHERE role='test_operator' LIMIT 1")["principal_id"])
    items = rows(conn, "SELECT * FROM app.item ORDER BY code")
    for day in days(start, end):
        now = at(day, 12)
        conn.execute("UPDATE app.scope SET simulation_at=%s WHERE id=%s", (now, scope))
        for order in rows(conn, "SELECT DISTINCT o.* FROM app.purchase_order o JOIN app.purchase_order_line l ON l.order_id=o.id WHERE o.status='accepted' AND l.expected_at<=%s AND l.received_units+l.cancelled_units<l.ordered_units", (now,)):
            lines = rows(conn, "SELECT * FROM app.purchase_order_line WHERE order_id=%s AND expected_at<=%s AND received_units+cancelled_units<ordered_units", (order["id"], now))
            body = {"source_receipt_key": f"auto:{order['order_key']}:{day}", "occurred_at": at(day).isoformat(), "lines": [
                {"order_line_id": str(line["id"]), "quantity": str(line["ordered_units"]-line["received_units"]-line["cancelled_units"]), "unit": line["unit"]} for line in lines]}
            receipt, receipt_job = receive(conn, scope, str(order["id"]), body)
            apply_receipt(conn, scope, {"resource_id": receipt, "id": receipt_job})
            conn.execute("UPDATE app.job SET status='succeeded',result=%s WHERE id=%s", (Jsonb({"receipt_id": receipt}), receipt_job))
        for item in items:
            inventory = one(conn, "SELECT * FROM app.inventory_state WHERE item_id=%s", (item["id"],))
            need = requested(state["seed"], item, day)
            filled = min(need, inventory["on_hand"] - inventory["unavailable"] - inventory["allocated"])
            version = inventory["version"] + 1
            event = None
            if filled > 0:
                event = insert(conn, "inventory_event", scope, item_id=item["id"], source_key=f"consume:{item['id']}:{day}",
                               event_kind="consumption", occurred_at=now, delta_on_hand=-filled, affected_version=version)
                conn.execute("UPDATE app.inventory_state SET on_hand=on_hand-%s,version=%s,as_of=%s WHERE id=%s", (filled, version, now, inventory["id"]))
            insert(conn, "demand_outcome", scope, item_id=item["id"], day=day, source_key=f"demand:{item['id']}:{day}",
                   occurred_at=now, requested_units=need, fulfilled_units=filled, lost_units=need-filled,
                   unit=item["base_unit"], inventory_version=version if filled else inventory["version"], inventory_event_id=event)
    conn.execute("UPDATE app.proposal SET status='expired' WHERE status='pending' AND expires_at<=%s", (at(end)-timedelta(minutes=30),))
    conn.execute("UPDATE app.scope SET simulation_at=%s WHERE id=%s", (at(end)-timedelta(minutes=30), scope))
    body = make_delivery(conn, scope, start, end, False)
    delivery, ingest = store_delivery(conn, scope, body, principal)
    return {"delivery_id": delivery, "child_job_ids": [ingest]}
