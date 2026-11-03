"""Scoped evaluation API using the approved JSON request/response schemas."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import jwt
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
import psycopg

from src.operations.business import decide, receive, release, reverse
from src.operations.contracts import SPEC, Conflict, at, parsed, validate
from src.operations.db import audit, digest, enqueue, insert, one, plain, rows, transaction
from src.operations.synthetic import store_delivery

app = FastAPI(title="TimesFM synthetic logistics", docs_url="/docs")


class InvalidCursor(ValueError):
    """The signed scope/path/filter cursor could not be reused safely."""


def job_view(conn: object, row: dict) -> dict:
    attempt = conn.execute("SELECT heartbeat_at FROM app.job_attempt WHERE job_id=%s ORDER BY number DESC LIMIT 1", (row["id"],)).fetchone()
    result = {key: None for key in SPEC["components"]["schemas"]["JobResult"]["required"]}
    result.update({"order_ids": [], "child_job_ids": [], "required_resubmission_delivery_ids": []})
    result.update({key: value for key, value in row["result"].items() if key in result})
    return {"id": row["id"], "parent_job_id": row["parent_job_id"], "stage": row["stage"], "status": row["status"],
            "current_attempt": row["current_attempt"], "heartbeat_at": attempt["heartbeat_at"] if attempt else None,
            "retry_at": row["next_retry_at"], "error_code": row["error_code"], "retryable": row["status"] in ("failed", "retry_wait") and row["current_attempt"] < 4,
            "result": result, "updated_at": row["updated_at"]}


def scenario_view(row: dict) -> dict:
    return {"data_source": "records" if row["profile"]["name"] == "cheonan_records_v1" else "synthetic", "inventory_source": "not_registered" if row["profile"]["name"] == "cheonan_records_v1" else "synthetic", **{key: row[key] for key in ("id", "name", "status", "seed", "version", "simulation_at", "next_week_start", "model_version_id", "active_week_start", "last_completed_week_start", "blocking_code")}, "last_job_id": row["active_job_id"]}


def order_view(conn: object, row: dict) -> dict:
    result = {k: row[k] for k in ("id", "decision_id", "supplier_id", "order_key", "status", "receipt_status", "content_hash", "released_at", "supplier_order_ref", "accepted_at")}
    result["lines"] = [{**{k: l[k] for k in ("id", "proposal_line_id", "item_id", "unit", "packs", "ordered_units", "received_units", "expected_at")},
                        "remaining_units": Decimal(0) if row["released_at"] else l["ordered_units"]-l["received_units"]-l["cancelled_units"]}
                       for l in rows(conn, "SELECT * FROM app.purchase_order_line WHERE order_id=%s ORDER BY id", (row["id"],))]
    return result


def proposal_view(conn: object, row: dict) -> dict:
    result = {k: row[k] for k in ("id", "forecast_id", "version", "supersedes_id", "status", "content_hash", "decision_as_of", "expires_at", "next_demand_at", "blocking_codes")}
    result["lines"] = [{k: l[k] for k in SPEC["components"]["schemas"]["ProposalLine"]["required"]} for l in rows(conn, "SELECT l.*,i.name AS item_name FROM app.proposal_line l JOIN app.item i ON i.id=l.item_id WHERE l.proposal_id=%s ORDER BY l.id", (row["id"],))]
    return result


def delivery_view(conn: object, row: dict) -> dict:
    snapshot = conn.execute("SELECT id FROM app.input_snapshot WHERE delivery_id=%s", (row["id"],)).fetchone()
    job = one(conn, "SELECT id FROM app.job WHERE stage='ingest' AND resource_id=%s", (row["id"],))
    issues = one(conn, "SELECT count(*) AS n FROM app.validation_issue WHERE delivery_id=%s", (row["id"],))
    return {**{k: row[k] for k in ("id", "kind", "week_start", "week_end_exclusive", "source_revision", "source_closed_at", "source_available_at", "received_at", "status")},
            "supersedes_delivery_id": row["supersedes_id"], "job_id": job["id"], "snapshot_id": snapshot["id"] if snapshot else None,
            "artifact_hash": row["body_hash"], "issue_count": issues["n"], "correction_impacts": [
                {"affected_delivery_id": r["affected_delivery_id"], "snapshot_id": r["snapshot_id"], "status": r["status"],
                 "resolution_delivery_id": r["resolution_delivery_id"], "reason_code": r["reason_code"]}
                for r in rows(conn, "SELECT * FROM app.delivery_impact WHERE correction_delivery_id=%s ORDER BY id", (row["id"],))]}


def forecast_view(conn: object, row: dict) -> dict:
    snapshot = one(conn, "SELECT * FROM app.input_snapshot WHERE id=%s", (row["snapshot_id"],))
    model = one(conn, "SELECT * FROM app.model_version WHERE id=%s", (row["model_version_id"],))
    return {**{k: row[k] for k in ("id", "snapshot_id", "model_version_id", "generated_at", "status", "validity", "job_id")},
            "forecast_origin": snapshot["forecast_origin"], "input_cutoff_at": snapshot["cutoff_at"], "knowledge_as_of": snapshot["knowledge_as_of"],
            "horizon_days": 14, "expected_values": 14*(model["expected_drugs"]+model["expected_materials"]),
            "stored_values": one(conn, "SELECT count(*) AS n FROM app.forecast_value WHERE forecast_id=%s", (row["id"],))["n"]}


def receipt_view(conn: object, row: dict) -> dict:
    return {**{k: row[k] for k in ("id", "order_id", "source_receipt_key", "occurred_at", "status", "reverses_receipt_id", "job_id")},
            "lines": [{k: l[k] for k in ("order_line_id", "quantity", "unit")} for l in rows(conn, "SELECT * FROM app.receipt_line WHERE receipt_id=%s ORDER BY id", (row["id"],))]}


def accepted(resource: str, job: str) -> dict:
    return {"resource_id": resource, "job_id": job, "status": "queued", "status_url": f"/api/v1/jobs/{job}", "request_id": str(uuid4())}


def page(request: Request, source: list[dict], binding_scope: str | None = None) -> dict:
    limit = int(request.query_params.get("limit", 50))
    if not 1 <= limit <= 100:
        raise ValueError("INVALID_LIMIT")
    cursor = request.query_params.get("cursor")
    filters = {key: value for key, value in request.query_params.items() if key not in ("cursor", "limit")}
    for key, value in filters.items():
        if key in ("date_from", "date_to"):
            field = "target_day" if source and "target_day" in source[0] else "day"
            source = [r for r in source if str(r[field]) >= value] if key == "date_from" else [r for r in source if str(r[field]) <= value]
        else:
            source = [r for r in source if str(r.get(key)) == value]
    def sort_key(row: dict) -> tuple[str, ...]:
        if "target_day" in row:
            return str(row["target_day"]), str(row["item_id"]), str(row["id"])
        return str(row.get("_created_at", row.get("created_at", ""))), str(row["id"])
    binding = {"scope": binding_scope or request.headers["X-Workspace-ID"], "path": request.url.path, "filters": filters}
    if cursor:
        try:
            claims = jwt.decode(cursor, os.environ["JWT_SECRET"], algorithms=["HS256"], issuer="paper-pagination", audience="paper-pagination")
            if any(claims.get(key) != value for key, value in binding.items()):
                raise InvalidCursor()
            last = tuple(claims["last"])
            source = [row for row in source if sort_key(row) > last]
        except (jwt.InvalidTokenError, KeyError, TypeError):
            raise InvalidCursor() from None
    source = sorted(source, key=sort_key)
    selected = source[:limit]
    next_cursor = jwt.encode({**binding, "last": sort_key(selected[-1]), "iss": "paper-pagination", "aud": "paper-pagination"}, os.environ["JWT_SECRET"], algorithm="HS256") if len(source) > limit else None
    for row in selected:
        row.pop("_created_at", None)
    return {"items": selected, "next_cursor": next_cursor}


def list_created_scenarios(request: Request, principal: dict) -> dict:
    """List only this creator's scenarios, rechecking live scoped membership."""
    if principal["platform_role"] != "test_admin":
        raise PermissionError()
    with transaction() as conn:
        candidates = rows(conn, "SELECT scope_id,created_at FROM app.scenario_creation_request WHERE principal_id=%s ORDER BY created_at,scope_id", (principal["id"],))
    visible = []
    for candidate in candidates:
        with transaction(str(candidate["scope_id"]), lock=False) as conn:
            roles = {r["role"] for r in rows(conn, "SELECT role FROM app.membership WHERE principal_id=%s", (principal["id"],))}
            if not roles.intersection({"test_operator", "admin"}):
                continue
            state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (candidate["scope_id"],))
            visible.append({**scenario_view(state), "_created_at": candidate["created_at"]})
    return page(request, visible, binding_scope="principal:"+str(principal["id"]))


def read_route(conn: object, request: Request, path: str, scope: str) -> tuple[int, dict]:
    parts = path.strip("/").split("/")
    identifier = request.path_params
    if parts[0] == "test":
        state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (identifier["scenario_id"],))
        if str(state["id"]) != scope:
            raise LookupError("NOT_FOUND")
        if parts[-1] == "demand-outcomes":
            data = rows(conn, "SELECT * FROM app.demand_outcome WHERE occurred_at<=%s ORDER BY id", (state["simulation_at"],))
            view = [{**{k: row[k] for k in SPEC["components"]["schemas"]["DemandOutcome"]["required"]}, "_created_at": row["created_at"]} for row in data]
            return 200, page(request, view)
        return 200, scenario_view(state)
    if parts[0] == "model-versions":
        model = one(conn, "SELECT * FROM app.model_version WHERE id=%s", (identifier["model_version_id"],))
        return 200, {**{k: model[k] for k in ("id", "status", "horizon_days", "drug_context_days", "material_context_days", "patient_context_days", "expected_drugs", "expected_materials", "expected_patients", "expected_known")},
                     "items": rows(conn, "SELECT m.item_id,m.cohort,m.ordinal,i.base_unit AS unit FROM app.model_item m JOIN app.item i ON i.id=m.item_id WHERE model_version_id=%s ORDER BY cohort,ordinal", (model["id"],)),
                     "features": rows(conn, "SELECT kind,feature_key,ordinal,definition FROM app.model_feature WHERE model_version_id=%s ORDER BY kind,ordinal", (model["id"],))}
    if parts[0] == "inventory":
        data = rows(conn, "SELECT s.*,i.base_unit AS unit FROM app.inventory_state s JOIN app.item i ON i.id=s.item_id ORDER BY s.id")
        view = []
        for row in data:
            if request.query_params.get("item_id") and str(row["item_id"]) != request.query_params["item_id"]:
                continue
            orders = rows(conn, "SELECT l.*,o.status,o.id AS order_id FROM app.purchase_order_line l JOIN app.purchase_order o ON o.id=l.order_id WHERE l.item_id=%s AND o.released_at IS NULL AND o.status NOT IN ('voided','rejected')", (row["item_id"],))
            view.append({**{k: row[k] for k in ("item_id", "unit", "as_of", "version", "on_hand", "unavailable", "allocated")},
                         "id": row["id"], "_created_at": row["created_at"], "available_units": row["on_hand"]-row["unavailable"]-row["allocated"],
                         "confirmed_incoming_units": sum((l["ordered_units"]-l["received_units"]-l["cancelled_units"] for l in orders if l["status"] == "accepted"), Decimal(0)),
                         "pending_order_units": sum((l["ordered_units"]-l["received_units"]-l["cancelled_units"] for l in orders if l["status"] != "accepted"), Decimal(0)),
                         "unknown_order_ids": [l["order_id"] for l in orders if l["status"] in ("submission_unknown", "sending")], "blocking_codes": []})
        result = page(request, view)
        for row in result["items"]:
            row.pop("id")
        return 200, result
    if parts[0] == "items" and parts[-1] == "forecast-history":
        from src.operations.forecast_history import forecast_history
        return 200, forecast_history(conn, scope, identifier["item_id"], request.query_params["forecast_id"])
    if parts[0] == "items":
        if parts[-1] == "ordering-policies":
            policies = rows(conn, "SELECT * FROM app.ordering_policy WHERE item_id=%s ORDER BY id", (identifier["item_id"],))
            return 200, page(request, [{**{k: p[k] for k in SPEC["components"]["schemas"]["OrderingPolicy"]["required"]}, "_created_at": p["created_at"]} for p in policies])
        data = rows(conn, "SELECT i.*,(SELECT p.id FROM app.ordering_policy p WHERE p.item_id=i.id ORDER BY version DESC LIMIT 1) AS current_policy_id FROM app.item i ORDER BY i.id")
        view = [{**{k: row[k] for k in SPEC["components"]["schemas"]["Item"]["required"]}, "_created_at": row["created_at"]} for row in data]
        if "item_id" in identifier:
            item = next((row for row in view if str(row["id"]) == identifier["item_id"]), None)
            if item is None:
                raise LookupError()
            item.pop("_created_at")
            return 200, item
        return 200, page(request, view)
    if parts[0] == "forecasts" and parts[-1] == "patient-history":
        from src.operations.forecast_history import patient_history
        return 200, patient_history(conn, scope, identifier["forecast_id"])
    if parts[0] == "forecasts" and parts[-1] == "values":
        one(conn, "SELECT id FROM app.forecast_run WHERE id=%s", (identifier["forecast_id"],))
        data = rows(conn, "SELECT v.*,i.name AS item_name,i.base_unit AS unit FROM app.forecast_value v JOIN app.item i ON i.id=v.item_id WHERE forecast_id=%s ORDER BY v.id", (identifier["forecast_id"],))
        result = page(request, data)
        result["items"] = [{k: row[k] for k in SPEC["components"]["schemas"]["ForecastValue"]["required"]} for row in result["items"]]
        return 200, result
    if parts[0] == "forecasts" and parts[-1] == "evaluations":
        one(conn, "SELECT id FROM app.forecast_run WHERE id=%s", (identifier["forecast_id"],))
        data = rows(conn, 'SELECT *,evaluation_window AS "window" FROM app.forecast_evaluation WHERE forecast_id=%s ORDER BY id', (identifier["forecast_id"],))
        return 200, page(request, [{**{k: row[k] for k in SPEC["components"]["schemas"]["ForecastEvaluation"]["required"]}, "_created_at": row["created_at"]} for row in data])
    if parts[0] == "weekly-deliveries" and parts[-1] == "issues":
        data = rows(conn, "SELECT * FROM app.validation_issue WHERE delivery_id=%s ORDER BY id", (identifier["delivery_id"],))
        return 200, page(request, [{**{k: row[k] for k in SPEC["components"]["schemas"]["ValidationIssue"]["required"]}, "_created_at": row["created_at"]} for row in data])
    if parts[0] == "logs":
        data = rows(conn, "SELECT *,created_at AS ingested_at FROM app.app_log ORDER BY id")
        result = page(request, data)
        result["items"] = [{k: row[k] for k in SPEC["components"]["schemas"]["Log"]["required"]} for row in result["items"]]
        return 200, result
    tables = {"jobs": ("job", job_view), "forecasts": ("forecast_run", forecast_view), "order-proposals": ("proposal", proposal_view),
              "purchase-orders": ("purchase_order", order_view), "weekly-deliveries": ("delivery", delivery_view), "receipts": ("receipt", receipt_view)}
    table, serializer = tables[parts[0]]
    if identifier:
        row = one(conn, f"SELECT * FROM app.{table} WHERE id=%s", (next(iter(identifier.values())),))
        return 200, serializer(conn, row)
    return 200, page(request, [{**serializer(conn, row), "_created_at": row["created_at"]} for row in rows(conn, f"SELECT * FROM app.{table} ORDER BY id")])


def mutate(conn: object, request: Request, path: str, scope: str, principal: dict, body: dict) -> tuple[int, dict]:
    params = request.path_params
    if path == "/weekly-deliveries":
        state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
        correction = body["supersedes_delivery_id"] is not None
        if correction:
            old = one(conn, "SELECT * FROM app.delivery WHERE id=%s AND kind='weekly'", (body["supersedes_delivery_id"],))
            if old["status"] != "validated" or str(old["week_start"]) != body["week_start"] or str(old["week_end_exclusive"]) != body["week_end_exclusive"] or body["source_revision"] != old["source_revision"]+1:
                raise Conflict("DELIVERY_REVISION_CONFLICT")
            if state["status"] not in ("ready", "failed", "advancing"):
                raise Conflict("SCENARIO_BUSY")
        elif state["status"] != "ready" or str(state["next_week_start"]) != body["week_start"] or body["source_revision"] != 1:
            raise Conflict("WEEK_VERSION_CONFLICT")
        if parsed(body["source_available_at"]) > datetime.now(timezone.utc):
            raise Conflict("FUTURE_AVAILABILITY")
        resource, job = store_delivery(conn, scope, body, str(principal["id"]))
        conn.execute("UPDATE app.scope SET status='advancing',active_week_start=%s,simulation_at=greatest(simulation_at,%s),active_job_id=%s,blocking_code=NULL WHERE id=%s", (body["week_start"], body["source_available_at"], job, scope))
        return 202, accepted(resource, job)
    if path.endswith("/weekly-deliveries"):
        if params["scenario_id"] != scope:
            raise LookupError()
        state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
        if state["status"] != "ready" or state["version"] != body["expected_version"] or str(state["next_week_start"]) != body["week_start"]:
            raise Conflict("WEEK_VERSION_CONFLICT")
        job = enqueue(conn, scope, "simulate", scope, key=f"simulate:{body['week_start']}")
        conn.execute("UPDATE app.scope SET status='advancing',active_job_id=%s,active_week_start=%s WHERE id=%s", (job, body["week_start"], scope))
        return 202, accepted(scope, job)
    if path.endswith("/decisions"):
        return 200, decide(conn, scope, params["proposal_id"], str(principal["id"]), body)
    if path.endswith("/receipts"):
        receipt, job = receive(conn, scope, params["order_id"], body)
        return 202, accepted(receipt, job)
    if path.endswith("/reversals"):
        resource, job = reverse(conn, scope, params["receipt_id"], body)
        return 202, accepted(resource, job)
    if path.endswith("/release"):
        return 200, order_view(conn, release(conn, scope, params["order_id"], str(principal["id"]), body))
    if path.endswith("/ordering-policies"):
        current = one(conn, "SELECT * FROM app.ordering_policy WHERE item_id=%s ORDER BY version DESC LIMIT 1", (params["item_id"],))
        if current["version"] != body["expected_version"]:
            raise Conflict("POLICY_VERSION_CONFLICT")
        if not body["calendar_rule"]["cutoff_local"].endswith(":00"):
            raise Conflict("WHOLE_HOUR_CUTOFF_REQUIRED")
        values = {k: v for k, v in body.items() if k != "expected_version"}
        resource = insert(conn, "ordering_policy", scope, item_id=params["item_id"], version=current["version"]+1, policy_hash=digest(values), **values)
        policy = one(conn, "SELECT * FROM app.ordering_policy WHERE id=%s", (resource,))
        audit(conn, scope, "policy.updated", resource, {"version": policy["version"]}, str(principal["id"]))
        return 201, {k: policy[k] for k in SPEC["components"]["schemas"]["OrderingPolicy"]["required"]}
    if path.endswith("/retry"):
        job = one(conn, "SELECT * FROM app.job WHERE id=%s", (params["job_id"],))
        if job["current_attempt"] != body["expected_attempt"] or job["current_attempt"] >= 4 or job["status"] != "failed" or job["error_code"] in ("INPUT_REJECTED", "INPUT_REVISED", "INSUFFICIENT_HISTORY"):
            raise Conflict("JOB_NOT_RETRYABLE")
        queue = "ml" if job["stage"] == "forecast" else "ingest" if job["stage"] == "ingest" else "business"
        insert(conn, "outbox", scope, job_id=job["id"], message_id=str(uuid4()), attempt=job["current_attempt"]+1,
               message_type=f"command.{job['stage']}", routing_key=queue, payload={"scope_id": scope, "job_id": str(job["id"]), "attempt": job["current_attempt"]+1}, status="pending", available_at=datetime.now(timezone.utc))
        conn.execute("UPDATE app.job SET status='retry_wait',next_retry_at=now(),updated_at=now() WHERE id=%s", (job["id"],))
        if job["stage"] in ("bootstrap", "ingest", "forecast", "proposal", "simulate"):
            conn.execute("UPDATE app.scope SET status=CASE WHEN active_week_start IS NULL THEN 'bootstrapping' ELSE 'advancing' END,blocking_code=NULL,active_job_id=%s WHERE id=%s", (job["id"], scope))
        return 202, accepted(str(job["resource_id"]), str(job["id"]))
    if path.endswith("/reconcile"):
        order = one(conn, "SELECT * FROM app.purchase_order WHERE id=%s", (params["order_id"],))
        if order["status"] not in ("submission_unknown", "sending"):
            raise Conflict("ORDER_NOT_UNKNOWN")
        job = enqueue(conn, scope, "reconcile", params["order_id"], key=f"reconcile:{uuid4()}")
        return 202, accepted(params["order_id"], job)
    if path.endswith("/recalculate"):
        proposed = one(conn, "SELECT * FROM app.proposal WHERE id=%s", (params["proposal_id"],))
        if proposed["version"] != body["expected_version"]:
            raise Conflict("PROPOSAL_VERSION_CONFLICT")
        job = enqueue(conn, scope, "proposal", str(proposed["forecast_id"]), key=f"proposal:{uuid4()}")
        return 202, accepted(params["proposal_id"], job)
    raise Conflict("OPERATION_NOT_ENABLED")


def create_scenario(request: Request, principal: dict, body: dict, idempotency_body: dict | None = None) -> tuple[int, dict]:
    if principal["platform_role"] != "test_admin":
        raise PermissionError()
    start = date.fromisoformat(body["first_week_start"])
    if start.weekday() != 0 or start-timedelta(days=body["initial_history_days"]) < date(2020, 1, 1) or start+timedelta(days=21) >= date(2025, 1, 1):
        raise Conflict("CALENDAR_RANGE_OR_WEEK_UNSUPPORTED")
    if body["profile"] == "cheonan_records_v1":
        from src.operations.records import validate_records_window
        validate_records_window(start-timedelta(days=body["initial_history_days"]), start+timedelta(days=7))
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
    with transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"create:{principal['id']}:{key}",))
        old = conn.execute("SELECT * FROM app.scenario_creation_request WHERE principal_id=%s AND request_key=%s", (principal["id"], key)).fetchone()
        if old:
            if old["request_hash"] != digest(idempotency_body if idempotency_body is not None else body):
                raise Conflict("IDEMPOTENCY_KEY_CONFLICT")
            return old["http_status"], old["response_body"]
        scope = str(uuid4())
        conn.execute("SELECT set_config('app.scope_id',%s,true)", (scope,))
        insert(conn, "scope", id=scope, hospital_code=body["hospital_code"], warehouse_code=body["warehouse_code"], name=body["name"], seed=body["seed"], simulation_at=at(start)-timedelta(minutes=30), next_week_start=start,
               profile={"name": body["profile"], "history_days": body["initial_history_days"]})
        for role in ("viewer", "uploader", "approver", "operator", "admin", "receiver", "test_operator", "log_reader"):
            insert(conn, "membership", scope, principal_id=principal["id"], role=role)
        job = enqueue(conn, scope, "bootstrap", scope)
        conn.execute("UPDATE app.scope SET active_job_id=%s WHERE id=%s", (job, scope))
        response = {"scenario_id": scope, "job_id": job, "status": "queued", "status_url": f"/api/v1/jobs/{job}", "request_id": str(uuid4())}
        insert(conn, "scenario_creation_request", principal_id=principal["id"], request_key=key, request_hash=digest(idempotency_body if idempotency_body is not None else body), scope_id=scope, job_id=job, http_status=202, response_body=response)
        if "reset_of" in body:
            audit(conn, scope, "scenario.reset_last_week", scope, {"source_scope_id": body["reset_of"], "week_start": body["first_week_start"]}, str(principal["id"]))
        return 202, response


def reset_last_week(request: Request, principal: dict, body: dict) -> tuple[int, dict]:
    """Prepare a fresh test scope before the last completed week; preserve all source history."""
    if principal["platform_role"] != "test_admin":
        raise PermissionError()
    scope = str(UUID(request.headers.get("X-Workspace-ID", "")))
    if scope != request.path_params["scenario_id"]:
        raise LookupError("NOT_FOUND")
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
    identity = {"operation": "reset-last-week", "scope_id": scope, **body}
    with transaction(scope) as conn:
        roles = {row["role"] for row in rows(conn, "SELECT role FROM app.membership WHERE principal_id=%s", (principal["id"],))}
        if not roles:
            raise LookupError("NOT_FOUND")
        if not roles.intersection({"test_operator", "admin"}):
            raise PermissionError()
        state = one(conn, "SELECT * FROM app.scope WHERE id=%s", (scope,))
        prior = conn.execute("SELECT * FROM app.scenario_creation_request WHERE principal_id=%s AND request_key=%s", (principal["id"], key)).fetchone()
        if prior:
            if prior["request_hash"] != digest(identity):
                raise Conflict("IDEMPOTENCY_KEY_CONFLICT")
            return prior["http_status"], prior["response_body"]
        if state["version"] != body["expected_version"]:
            raise Conflict("SCENARIO_VERSION_CONFLICT")
        if state["status"] != "ready" or one(conn, "SELECT count(*) AS n FROM app.job WHERE status IN ('queued','running','retry_wait')")["n"]:
            raise Conflict("SCENARIO_BUSY")
        if not state["last_completed_week_start"]:
            raise Conflict("NO_COMPLETED_WEEK")
        if state["profile"]["name"] != "cheonan_records_v1":
            raise Conflict("RECORDED_TEST_SCOPE_REQUIRED")
        setup = {"name": state["name"][:120], "hospital_code": state["hospital_code"], "warehouse_code": state["warehouse_code"],
                 "seed": state["seed"], "first_week_start": str(state["last_completed_week_start"]),
                 "initial_history_days": state["profile"]["history_days"], "profile": state["profile"]["name"], "reset_of": scope}
        return create_scenario(request, principal, setup, idempotency_body=identity)


def endpoint(path: str, method: str, operation: dict) -> Any:
    async def execute(request: Request) -> JSONResponse:
        request_id = str(uuid4())
        status = 500
        scope = None
        try:
            if path.startswith("/health/"):
                checks = {key: "not_checked" for key in ("database", "artifact_storage", "broker", "schema")}
                status = 200
                if path.endswith("ready"):
                    bearer = request.headers.get("Authorization", "")
                    if not bearer.startswith("Bearer "):
                        raise jwt.InvalidTokenError()
                    claims = jwt.decode(bearer[7:], os.environ["JWT_SECRET"], algorithms=["HS256"], issuer="paper-operations", audience="paper-api", options={"require": ["exp", "iat", "sub"]})
                    with transaction() as conn:
                        principal = one(conn, "SELECT * FROM app.principal WHERE subject=%s AND NOT disabled", (claims["sub"],))
                    if principal["platform_role"] != "test_admin":
                        raise PermissionError()
                    from src.operations.messaging import connection
                    try:
                        with transaction() as conn:
                            conn.execute("SELECT 1 FROM app.job LIMIT 0")
                            conn.execute("SELECT 1 FROM app.artifact_payload LIMIT 0")
                        checks.update(database="ok", schema="ok", artifact_storage="ok")
                    except Exception:
                        checks.update(database="failed", schema="failed", artifact_storage="failed")
                        status = 503
                    try:
                        broker = connection()
                        broker.close()
                        checks["broker"] = "ok"
                    except Exception:
                        checks["broker"] = "degraded"
                        status = 503
                result = {"status": "ok" if status == 200 else "not_ready", "checks": checks, "request_id": request_id}
            else:
                bearer = request.headers.get("Authorization", "")
                if not bearer.startswith("Bearer "):
                    raise jwt.InvalidTokenError()
                claims = jwt.decode(bearer[7:], os.environ["JWT_SECRET"], algorithms=["HS256"], issuer="paper-operations", audience="paper-api", options={"require": ["exp", "iat", "sub"]})
                with transaction() as conn:
                    principal = one(conn, "SELECT * FROM app.principal WHERE subject=%s AND NOT disabled", (claims["sub"],))
                body = {}
                for parameter in operation.get("parameters", []):
                    parameter = SPEC["components"]["parameters"][parameter["$ref"].rsplit("/", 1)[-1]] if "$ref" in parameter else parameter
                    collection = request.headers if parameter["in"] == "header" else request.query_params if parameter["in"] == "query" else request.path_params
                    value = collection.get(parameter["name"])
                    if value is None:
                        if parameter.get("required"):
                            raise ValueError("REQUIRED_PARAMETER")
                        continue
                    value = int(value) if parameter["schema"].get("type") == "integer" else value
                    Draft202012Validator(parameter["schema"], format_checker=FormatChecker()).validate(value)
                if method == "post":
                    raw = bytearray()
                    async for chunk in request.stream():
                        raw.extend(chunk)
                        if len(raw) > 2*1024*1024:
                            raise ValueError("PAYLOAD_TOO_LARGE")
                    body = json.loads(raw)
                    schema_name = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
                    validate(schema_name, body)
                if path == "/me":
                    roles = []
                    workspace = request.headers.get("X-Workspace-ID")
                    if workspace:
                        scope = str(UUID(workspace))
                        with transaction(scope, lock=False) as conn:
                            roles = sorted(r["role"] for r in rows(conn, "SELECT role FROM app.membership WHERE principal_id=%s", (principal["id"],)))
                        if not roles:
                            raise LookupError()
                    status, result = 200, {"id": principal["id"], "platform_role": principal["platform_role"], "workspace_id": scope, "roles": roles}
                elif path == "/test/scenarios" and method == "get":
                    status, result = 200, list_created_scenarios(request, principal)
                elif path == "/test/scenarios":
                    status, result = create_scenario(request, principal, body)
                elif path.endswith("/reset-last-week"):
                    status, result = reset_last_week(request, principal, body)
                    scope = str(UUID(request.headers.get("X-Workspace-ID", "")))
                else:
                    scope = str(UUID(request.headers.get("X-Workspace-ID", "")))
                    with transaction(scope, lock=method == "post") as conn:
                        memberships = {row["role"] for row in rows(conn, "SELECT role FROM app.membership WHERE principal_id=%s", (principal["id"],))}
                        if not memberships:
                            raise LookupError()
                        required = "viewer"
                        if path == "/logs":
                            required = "log_reader"
                        if path.startswith("/test/"):
                            required = "test_operator"
                        if method == "post":
                            required = "test_operator" if path.startswith("/test/") else "approver" if path.endswith("decisions") else "admin" if path.endswith("ordering-policies") else "receiver" if path.endswith(("receipts", "reversals")) else "uploader" if path == "/weekly-deliveries" else "operator"
                        permitted = required in memberships or "admin" in memberships
                        if method == "get" and path.startswith("/jobs"):
                            permitted = permitted or "operator" in memberships
                        if method == "get" and path.endswith("/input-contract"):
                            permitted = permitted or "uploader" in memberships
                        if not permitted:
                            raise PermissionError()
                        if method == "get":
                            status, result = read_route(conn, request, path, scope)
                        else:
                            key = request.headers.get("Idempotency-Key")
                            if not key:
                                raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
                            method_path = f"POST {request.url.path}"
                            old = conn.execute("SELECT * FROM app.api_idempotency WHERE principal_id=%s AND method_path=%s AND request_key=%s", (principal["id"], method_path, key)).fetchone()
                            if old:
                                if old["request_hash"] != digest(body):
                                    raise Conflict("IDEMPOTENCY_KEY_CONFLICT")
                                status, result = old["http_status"], old["response_body"]
                            else:
                                status, result = mutate(conn, request, path, scope, principal, body)
                                response_name = operation["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
                                validate(response_name, plain(result))
                                insert(conn, "api_idempotency", scope, principal_id=principal["id"], method_path=method_path, request_key=key, request_hash=digest(body), http_status=status, response_body=plain(result))
            result = plain(result)
            response_ref = operation["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"]
            validate(response_ref.rsplit("/", 1)[-1], result)
            if scope and method == "post":
                from src.operations.messaging import log

                log(scope, "api", "API_ACCEPTED", "API mutation committed", context={"operation": operation["operationId"]})
        except Exception as error:
            if isinstance(error, jwt.InvalidTokenError):
                status, code = 401, "UNAUTHENTICATED"
            elif isinstance(error, InvalidCursor):
                status, code = 400, "INVALID_CURSOR"
            elif isinstance(error, PermissionError):
                status, code = 403, "FORBIDDEN"
            elif type(error) is LookupError:
                status, code = 404, "NOT_FOUND"
            elif isinstance(error, (Conflict, psycopg.IntegrityError)):
                status, code = 409, str(error) if isinstance(error, Conflict) else "CONSTRAINT_CONFLICT"
            elif isinstance(error, (ValueError, ValidationError)):
                status, code = (413, "PAYLOAD_TOO_LARGE") if str(error) == "PAYLOAD_TOO_LARGE" else (422, "INVALID_REQUEST")
            else:
                status, code = 500, "INTERNAL_ERROR"
            result = {"request_id": request_id, "error": {"code": code, "message": code, "retryable": status == 500, "fields": []}}
            from src.operations.messaging import log
            if scope:
                log(scope, "api", code, "API request failed", context={"type": type(error).__name__})
        headers = {"X-Request-ID": request_id}
        if status == 202:
            headers.update(Location=result["status_url"], **{"Retry-After": "1"})
        return JSONResponse(result, status_code=status, headers=headers)
    return execute


# Phase-one routes are deliberately advertised only after their handler is implemented.
ENABLED_POST = {path for path, methods in SPEC["paths"].items() if "post" in methods}
for route_path, operations in SPEC["paths"].items():
    for route_method, definition in operations.items():
        if route_method == "get" or route_path in ENABLED_POST:
            app.add_api_route("/api/v1"+route_path, endpoint(route_path, route_method, definition), methods=[route_method.upper()], name=definition["operationId"])


def runtime_openapi() -> dict:
    document = json.loads(json.dumps(SPEC))
    document["paths"] = {path: {method: operation for method, operation in methods.items()
                               if method == "get" or path in ENABLED_POST}
                         for path, methods in document["paths"].items()}
    document["paths"] = {path: methods for path, methods in document["paths"].items() if methods}
    return document


app.openapi = runtime_openapi
