"""Inject recoverable failures into the isolated Compose evaluation stack."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from system_scenario import Client

ROOT = Path(__file__).resolve().parents[2]


def compose(*args: str, env: dict | None = None, stdin: str | None = None) -> str:
    result = subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", *args], cwd=ROOT,
                            input=stdin, text=True, capture_output=True, env={**os.environ, **(env or {})}, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:])
    return result.stdout.strip()


def sql(statement: str) -> str:
    return compose("exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "migration", "-d", "operations", "-Atc", statement)


def wait_status(client: Client, order: str, expected: str) -> dict:
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        value = client.get("/purchase-orders/"+order)
        if value["status"] == expected:
            return value
        time.sleep(.2)
    raise AssertionError(value)


def run(base: str, token: str, output: Path) -> dict:
    evidence = {"synthetic_only": True, "status": "running", "checks": []}
    started = time.monotonic()
    def passed(name: str) -> None:
        evidence["checks"].append(name)
        print(name, flush=True)
    try:
        admin = Client(base, token)
        created = admin.post("/test/scenarios", {"name": "장애 복구 검사", "seed": 20261022, "hospital_code": "SYNTHETIC", "warehouse_code": "FAULT",
                                               "first_week_start": "2024-03-04", "initial_history_days": 112, "profile": "smoke_v1"})
        client = Client(base, token, created["scenario_id"])
        scope = client.scope
        evidence["scope_id"] = scope
        client.wait_ready()
        # Persist API work while broker is down. No state may be lost after the API returns 202.
        compose("stop", "broker")
        command = client.post("/test/scenarios/"+scope+"/weekly-deliveries", {"expected_version": 1, "week_start": "2024-03-04"})
        assert sql(f"SELECT count(*) FROM app.outbox WHERE job_id='{command['job_id']}' AND status IN ('pending','leased')") == "1"
        compose("start", "broker")
        client.wait_ready()
        passed("broker_down_commit_outbox_recovery_actual_timesfm")
        proposal = max(client.all("/order-proposals"), key=lambda p: p["version"])
        # Logger outage must not prevent an approval; queued logs are drained after restart.
        compose("stop", "logger")
        compose("up", "-d", "--no-build", "supplier", env={"SUPPLIER_LOSE_FIRST_RESPONSE": "1"})
        decision = client.post("/order-proposals/"+proposal["id"]+"/decisions", {"proposal_version": proposal["version"], "content_hash": proposal["content_hash"], "decision": "approve"}, 200)
        order_id = decision["order_ids"][0]
        unknown = wait_status(client, order_id, "submission_unknown")
        assert sql(f"SELECT count(*) FROM supplier_sim.acceptance WHERE order_key='{unknown['order_key']}'") == "1"
        job = client.post("/purchase-orders/"+order_id+"/reconcile", {"reason": "응답 유실 접수 조회"})
        client.wait_job(job["job_id"])
        order = wait_status(client, order_id, "accepted")
        assert sql(f"SELECT count(*) FROM supplier_sim.acceptance WHERE order_key='{order['order_key']}'") == "1"
        assert sql(f"SELECT count(*) FROM app.order_attempt WHERE order_id='{order_id}' AND kind='submit'") == "1"
        assert client.request("/purchase-orders/"+order_id+"/release", {"expected_status": "blocked", "content_hash": order["content_hash"], "reason": "해제 금지 검사"})[0] == 409
        passed("supplier_acceptance_response_lost_lookup_same_key_no_resubmission")
        compose("start", "logger")
        deadline = time.monotonic()+15
        while time.monotonic() < deadline and not any(l["code"] == "API_ACCEPTED" for l in client.all("/logs")):
            time.sleep(.2)
        assert any(l["code"] == "API_ACCEPTED" for l in client.all("/logs"))
        passed("logger_outage_preserves_business_and_drains_queue")
        # Ordinary early receipt, full reversal, duplicate reversal, replacement receipt.
        line = order["lines"][0]
        before = next(i for i in client.all("/inventory") if i["item_id"] == line["item_id"])
        body = {"source_receipt_key": "fault-receipt", "occurred_at": order["accepted_at"], "lines": [{"order_line_id": line["id"], "quantity": line["ordered_units"], "unit": line["unit"]}]}
        receipt = client.post("/purchase-orders/"+order_id+"/receipts", body)
        client.wait_job(receipt["job_id"])
        reversal = {"source_receipt_key": "fault-reversal", "occurred_at": order["accepted_at"], "reason": "잘못된 입고 전체 정정"}
        reversed_receipt = client.post("/receipts/"+receipt["resource_id"]+"/reversals", reversal)
        client.wait_job(reversed_receipt["job_id"])
        after = next(i for i in client.all("/inventory") if i["item_id"] == line["item_id"])
        assert Decimal(before["on_hand"]) == Decimal(after["on_hand"])
        assert client.request("/receipts/"+receipt["resource_id"]+"/reversals", {**reversal, "source_receipt_key": "again"})[0] == 409
        passed("receipt_full_reversal_retains_history_no_second_reversal")
        # Duplicate command delivery uses completed job state rather than repeating a stock event.
        count = sql(f"SELECT count(*) FROM app.inventory_event WHERE scope_id='{scope}'")
        payload = json.dumps({"scope_id": scope, "job_id": receipt["job_id"], "attempt": 1})
        script = "from src.operations.messaging import connection;import pika;c=connection();ch=c.channel();ch.basic_publish(exchange='operations',routing_key='business',body="+repr(payload)+",properties=pika.BasicProperties(message_id='"+str(uuid4())+"',delivery_mode=2));c.close()"
        compose("exec", "-T", "business", "python", "-c", script)
        time.sleep(1)
        assert sql(f"SELECT count(*) FROM app.inventory_event WHERE scope_id='{scope}'") == count
        passed("duplicate_completed_command_has_no_stock_effect")
        # Correct the latest whole week. The input snapshot must be new, old values retained.
        delivery = next(d for d in client.all("/weekly-deliveries") if d["kind"] == "weekly")
        stored = sql(f"SELECT body::text FROM app.artifact_payload WHERE artifact_id=(SELECT artifact_id FROM app.delivery WHERE id='{delivery['id']}')")
        corrected = json.loads(stored)
        corrected.update(source_revision=2, supersedes_delivery_id=delivery["id"])
        corrected["usage"][0]["quantity"] = str(Decimal(corrected["usage"][0]["quantity"])+1)
        update = client.post("/weekly-deliveries", corrected)
        client.wait_job(update["job_id"])
        state = client.wait_ready()
        assert state["version"] == 2 and state["next_week_start"] == "2024-03-11"
        forecasts = client.all("/forecasts")
        assert len(forecasts) == 2 and sum(f["validity"] == "input_revised" for f in forecasts) == 1
        assert all(f["stored_values"] == 70 for f in forecasts)
        assert client.get("/weekly-deliveries/"+update["resource_id"])["correction_impacts"]
        passed("latest_revision_new_snapshot_old_forecast_retained_week_not_advanced_twice")
        # Sealed DB input guard and role boundaries are checked against the actual deployed database.
        snapshot = next(f["snapshot_id"] for f in forecasts if f["validity"] == "current")
        result = subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "exec", "-T", "db", "psql", "-v", "ON_ERROR_STOP=1", "-U", "migration", "-d", "operations", "-c", f"UPDATE app.snapshot_target SET quantity=0 WHERE snapshot_id='{snapshot}'"], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode != 0 and ("sealed" in result.stderr.lower() or "snapshot is not building" in result.stderr.lower()), result.stderr
        passed("sealed_snapshot_write_rejected_by_postgres")
        # Global role privilege proof, without disclosing credentials.
        assert sql("SELECT has_table_privilege('op_ml','app.decision','INSERT'),has_table_privilege('op_logger','app.purchase_order','INSERT'),has_table_privilege('op_api','app.forecast_value','INSERT')") == "f|f|f"
        passed("ml_logger_api_write_ownership")
        # Kill the actual ML container during a new forecast and expire its abandoned lease.
        state = client.get("/test/scenarios/"+scope)
        client.post("/test/scenarios/"+scope+"/weekly-deliveries", {"expected_version": state["version"], "week_start": state["next_week_start"]})
        deadline = time.monotonic()+60
        abandoned = None
        while time.monotonic() < deadline:
            jobs = client.all("/jobs")
            running = [j for j in jobs if j["stage"] == "forecast" and j["status"] == "running"]
            if running:
                abandoned = running[0]["id"]
                compose("kill", "timesfm")
                break
            time.sleep(.1)
        assert abandoned is not None
        old_token = sql(f"SELECT fencing_token FROM app.job_attempt WHERE job_id='{abandoned}' ORDER BY number DESC LIMIT 1")
        sql(f"UPDATE app.job_attempt SET lease_until=now()-interval '1 second' WHERE job_id='{abandoned}' AND status='running'")
        compose("start", "timesfm")
        client.wait_ready()
        recovered = client.get("/jobs/"+abandoned)
        assert recovered["status"] == "succeeded" and recovered["current_attempt"] == 2
        count = sql(f"SELECT count(*) FROM app.forecast_value WHERE forecast_id=(SELECT id FROM app.forecast_run WHERE job_id='{abandoned}')")
        assert count == "70"
        script = "from src.operations.db import transaction;from src.operations.ml_fence import assert_fence;from src.operations.contracts import Conflict;\ntry:\n with transaction('"+scope+"') as c: assert_fence(c,'"+abandoned+"','"+old_token+"')\nexcept Conflict: print('STALE_ATTEMPT_REJECTED')\n"
        assert "STALE_ATTEMPT_REJECTED" in compose("exec", "-T", "business", "python", "-c", script)
        passed("killed_real_ml_worker_lease_retry_new_token_and_single_output")
        evidence["status"] = "succeeded"
    except Exception as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        compose("start", "broker", "logger", "timesfm")
        compose("up", "-d", "--no-build", "supplier", env={"SUPPLIER_LOSE_FIRST_RESPONSE": "0"})
        evidence["elapsed_seconds"] = time.monotonic()-started
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8088/api/v1")
    parser.add_argument("--token-file", type=Path, default=Path("deploy/.state/test-token"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.base, args.token_file.read_text().strip(), args.output)
