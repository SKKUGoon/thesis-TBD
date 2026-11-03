"""Queue consumers, transactional outbox relay, attempt leases and isolated execution."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import random
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pika
import psycopg
from psycopg.types.json import Jsonb

from src.operations.contracts import Conflict
from src.operations.db import audit, insert, one, plain, rows, transaction
from src.operations.messaging import connection, declare, flush_logs, log
from src.operations.ml_fence import assert_fence


def scopes() -> list[str]:
    with transaction() as conn:
        return [str(row["list_scope_ids"]) for row in rows(conn, "SELECT app.list_scope_ids()")]


def retry(conn: object, scope: str, job: dict, code: str) -> None:
    """Close the old attempt and create at most three timed retry commands."""
    attempt = job["current_attempt"]
    conn.execute("UPDATE app.job_attempt SET status='failed',finished_at=now(),error_code=%s WHERE job_id=%s AND number=%s AND status='running'", (code, job["id"], attempt))
    permanent = code in ("INPUT_REVISED", "STALE_WEEK", "INSUFFICIENT_HISTORY", "INSUFFICIENT_PATIENT_HISTORY", "APPROVAL_WINDOW_CLOSED")
    if attempt >= 4 or permanent:
        conn.execute("UPDATE app.job SET status='failed',error_code=%s,updated_at=now() WHERE id=%s", (code, job["id"]))
        if job["stage"] in ("bootstrap", "ingest", "forecast", "proposal", "simulate"):
            # ML has no scope write privilege: recovery relay sets the terminal scope failure.
            if os.environ["ROLE"] != "ml":
                conn.execute("UPDATE app.scope SET status='failed',blocking_code=%s WHERE id=%s", (code, scope))
        if job["stage"] == "forecast":
            conn.execute("UPDATE app.forecast_run SET status='failed',manifest=jsonb_build_object('failure_code',%s::text) WHERE job_id=%s", (code, job["id"]))
        return
    delay = (10, 60, 300)[attempt-1] * random.uniform(.8, 1.2)
    available = datetime.now(timezone.utc)+timedelta(seconds=delay)
    conn.execute("UPDATE app.job SET status='retry_wait',error_code=%s,next_retry_at=%s,updated_at=now() WHERE id=%s", (code, available, job["id"]))
    queue = "ml" if job["stage"] == "forecast" else "ingest" if job["stage"] == "ingest" else "business"
    insert(conn, "outbox", scope, job_id=job["id"], message_id=str(uuid4()), attempt=attempt+1,
           message_type=f"command.{job['stage']}", routing_key=queue,
           payload={"scope_id": scope, "job_id": str(job["id"]), "attempt": attempt+1}, status="pending", available_at=available)


def relay() -> None:
    """Publish after business commit, confirm then mark; recover abandoned work leases."""
    conn = connection()
    channel = conn.channel()
    declare(channel)
    channel.confirm_delivery()
    while True:
        for scope in scopes():
            with transaction(scope) as db:
                for job in rows(db, "SELECT j.* FROM app.job j JOIN app.job_attempt a ON a.job_id=j.id AND a.number=j.current_attempt WHERE j.status='running' AND a.lease_until<now()"):
                    retry(db, scope, job, "LEASE_EXPIRED")
                db.execute("UPDATE app.scope SET status='failed',blocking_code='PIPELINE_FAILED' WHERE id=%s AND status IN ('advancing','bootstrapping') AND EXISTS(SELECT 1 FROM app.job WHERE status='failed' AND stage IN ('bootstrap','ingest','forecast','proposal','simulate') AND created_at>=(SELECT created_at FROM app.job WHERE id=app.scope.active_job_id))", (scope,))
                # Recover a broker restart even if a published-but-unconsumed command disappeared.
                db.execute("UPDATE app.outbox SET status='pending',available_at=now() WHERE status='published' AND published_at<now()-interval '60 seconds' AND job_id IN (SELECT id FROM app.job WHERE status IN ('queued','retry_wait'))")
                messages = rows(db, "SELECT * FROM app.outbox WHERE (status='pending' AND available_at<=now()) OR (status='leased' AND lease_until<now()) ORDER BY available_at LIMIT 20")
                lease = str(uuid4())
                for msg in messages:
                    db.execute("UPDATE app.outbox SET status='leased',lease_token=%s,lease_until=now()+interval '30 seconds' WHERE id=%s", (lease, msg["id"]))
            for msg in messages:
                channel.basic_publish(exchange="operations", routing_key=msg["routing_key"], body=json.dumps(msg["payload"]).encode(), mandatory=True,
                                      properties=pika.BasicProperties(message_id=str(msg["message_id"]), content_type="application/json", delivery_mode=2))
                with transaction(scope) as db:
                    db.execute("UPDATE app.outbox SET status='published',published_at=now(),publish_count=publish_count+1 WHERE id=%s AND lease_token=%s", (msg["id"], lease))
                log(scope, "relay", "OUTBOX_PUBLISHED", "Durable command confirmed by broker", str(msg["job_id"]))
        conn.process_data_events(time_limit=.3)


def execute_job(scope: str, job_id: str, token: str) -> None:
    """Run one fenced attempt, committing business results before consumer acknowledgement."""
    with transaction(scope, lock=False) as conn:
        job = one(conn, "SELECT * FROM app.job WHERE id=%s", (job_id,))
    try:
        if job["stage"] == "forecast":
            from src.operations.ml import forecast

            result = forecast(scope, job, token)
        elif job["stage"] in ("submit", "reconcile"):
            from src.operations.business import send

            result = send(scope, job, token)
        else:
            from src.operations.business import apply_receipt, proposal
            from src.operations.ingestion import ingest
            from src.operations.evaluation import evaluate
            from src.operations.synthetic import bootstrap, simulate

            handlers = {"bootstrap": bootstrap, "simulate": simulate, "ingest": ingest, "proposal": proposal, "receipt": apply_receipt, "evaluate": evaluate}
            with transaction(scope) as conn:
                assert_fence(conn, job_id, token)
                result = handlers[job["stage"]](conn, scope, job)
                # Handler effects and succeeded/failed state are committed together.
                status = "failed" if result.get("failure") else "succeeded"
                conn.execute("UPDATE app.job SET status=%s,result=%s,error_code=%s,updated_at=now() WHERE id=%s", (status, Jsonb(plain(result)), result.get("failure"), job_id))
                conn.execute("UPDATE app.job_attempt SET status=%s,finished_at=now() WHERE job_id=%s AND fencing_token=%s", ("failed" if result.get("failure") else "succeeded", job_id, token))
        if job["stage"] in ("submit", "reconcile"):
            with transaction(scope) as conn:
                assert_fence(conn, job_id, token)
                conn.execute("UPDATE app.job SET status='succeeded',result=%s,updated_at=now() WHERE id=%s", (Jsonb(plain(result)), job_id))
                conn.execute("UPDATE app.job_attempt SET status='succeeded',finished_at=now() WHERE job_id=%s AND fencing_token=%s", (job_id, token))
        log(scope, os.environ["ROLE"], "JOB_SUCCEEDED", "Job transaction committed", job_id, {"stage": job["stage"]})
    except Exception as error:
        code = str(error) if isinstance(error, Conflict) else type(error).__name__
        with transaction(scope) as conn:
            try:
                assert_fence(conn, job_id, token)
            except Conflict:
                return
            retry(conn, scope, job, code)
        log(scope, os.environ["ROLE"], "JOB_FAILED", "Job attempt failed", job_id, {"stage": job["stage"], "code": code, "detail": str(error)[:1000]})


def log_batches(conn: object, channel: object) -> None:
    """Commit at most 200 logs per batch/second, with bounded retries and periodic retention."""
    batch = []
    started = time.monotonic()
    cleanup_at = 0.
    for method, properties, body in channel.consume("logs", inactivity_timeout=1):
        if method is not None:
            try:
                event = json.loads(body)
                if len(body) > 16384 or event["level"] not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") or not isinstance(event["context"], dict):
                    raise ValueError("INVALID_LOG")
                batch.append((method, event, len(body)))
            except (KeyError, ValueError, TypeError):
                channel.basic_reject(method.delivery_tag, requeue=False)
        if batch and (len(batch) >= 200 or time.monotonic()-started >= 1 or method is None):
            groups = {}
            for entry in batch:
                groups.setdefault(entry[1]["scope_id"], []).append(entry)
            for scope, entries in groups.items():
                for number, backoff in enumerate((5, 10, 30)):
                    try:
                        with transaction(scope, lock=False) as db:
                            for _, event, size in entries:
                                db.execute("INSERT INTO app.app_log(scope_id,event_id,occurred_at,level,service,code,message,correlation_id,job_id,context,payload_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(scope_id,event_id) DO NOTHING", (scope, event["event_id"], event["occurred_at"], event["level"], event["service"], event["code"], event["message"], event["correlation_id"], event["job_id"], Jsonb(event["context"]), size))
                        for delivery, _, _ in entries:
                            channel.basic_ack(delivery.delivery_tag)
                        break
                    except (psycopg.IntegrityError, psycopg.DataError, KeyError, ValueError):
                        for delivery, _, _ in entries:
                            channel.basic_reject(delivery.delivery_tag, requeue=False)
                        break
                    except Exception:
                        conn.process_data_events(time_limit=backoff)
                        if number == 2:
                            for delivery, _, _ in entries:
                                channel.basic_nack(delivery.delivery_tag, requeue=True)
            batch = []
            started = time.monotonic()
        if time.monotonic() >= cleanup_at:
            for scope in scopes():
                with transaction(scope, lock=False) as db:
                    db.execute("DELETE FROM app.app_log WHERE id IN (SELECT id FROM app.app_log WHERE created_at<now()-interval '30 days' LIMIT 200)")
                    over_budget = one(db, "SELECT pg_total_relation_size('app.app_log')>1073741824 AS exceeded")["exceeded"]
                while over_budget:
                    # Let the bounded queue retain/drop technical logs while an operator reclaims space.
                    conn.process_data_events(time_limit=30)
                    with transaction(scope, lock=False) as db:
                        db.execute("DELETE FROM app.app_log WHERE id IN (SELECT id FROM app.app_log WHERE created_at<now()-interval '30 days' LIMIT 200)")
                        over_budget = one(db, "SELECT pg_total_relation_size('app.app_log')>1073741824 AS exceeded")["exceeded"]
            cleanup_at = time.monotonic()+60


def consume_job(conn: object, channel: object, method: object, properties: object, body: bytes) -> None:
    try:
        message = json.loads(body)
        scope, job_id = message["scope_id"], message["job_id"]
        with transaction(scope) as db:
            job = one(db, "SELECT * FROM app.job WHERE id=%s", (job_id,))
            expected_queue = "ml" if job["stage"] == "forecast" else "ingest" if job["stage"] == "ingest" else "business"
            if expected_queue != os.environ["ROLE"]:
                raise Conflict("WRONG_CONSUMER")
            if job["status"] not in ("queued", "retry_wait") or message["attempt"] != job["current_attempt"]+1:
                channel.basic_ack(method.delivery_tag)
                return
            if job["next_retry_at"] and job["next_retry_at"] > datetime.now(timezone.utc):
                channel.basic_nack(method.delivery_tag, requeue=True)
                return
            token = str(uuid4())
            db.execute("UPDATE app.job SET status='running',current_attempt=%s,updated_at=now() WHERE id=%s", (message["attempt"], job_id))
            insert(db, "job_attempt", scope, job_id=job_id, number=message["attempt"], fencing_token=token,
                   status="running", started_at=datetime.now(timezone.utc), heartbeat_at=datetime.now(timezone.utc), lease_until=datetime.now(timezone.utc)+timedelta(seconds=180))
        process = subprocess.Popen([sys.executable, "-m", "src.operations.worker", "--execute", scope, job_id, token], start_new_session=True)
        started = heartbeat = time.monotonic()
        while process.poll() is None:
            conn.process_data_events(time_limit=.5)
            if time.monotonic()-heartbeat >= 30:
                with transaction(scope, lock=False) as db:
                    db.execute("UPDATE app.job_attempt SET heartbeat_at=now(),lease_until=now()+interval '180 seconds' WHERE job_id=%s AND fencing_token=%s AND status='running'", (job_id, token))
                heartbeat = time.monotonic()
            if time.monotonic()-started > 1800:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                with transaction(scope) as db:
                    current = one(db, "SELECT * FROM app.job WHERE id=%s", (job_id,))
                    if current["status"] == "running":
                        retry(db, scope, current, "INFERENCE_TIMEOUT")
                break
        with transaction(scope) as db:
            current = one(db, "SELECT * FROM app.job WHERE id=%s", (job_id,))
            if current["status"] == "running":
                retry(db, scope, current, "WORKER_PROCESS_EXITED")
            db.execute("INSERT INTO app.inbox(scope_id,consumer,message_id,job_id,attempt,processed_at) VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(scope_id,consumer,message_id) DO NOTHING", (scope, os.environ["ROLE"], properties.message_id, job_id, message["attempt"]))
        channel.basic_ack(method.delivery_tag)
    except (ValueError, KeyError, LookupError, Conflict):
        channel.basic_reject(method.delivery_tag, requeue=False)


def run() -> None:
    while True:
        try:
            if os.environ["ROLE"] == "relay":
                relay()
            else:
                conn = connection()
                channel = conn.channel()
                declare(channel)
                channel.basic_qos(prefetch_count=200 if os.environ["ROLE"] == "logger" else 1)
                if os.environ["ROLE"] == "logger":
                    log_batches(conn, channel)
                else:
                    channel.basic_consume(queue=os.environ["ROLE"], on_message_callback=lambda ch, method, props, body: consume_job(conn, ch, method, props, body))
                    channel.start_consuming()
        except Exception:
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", nargs=3)
    args = parser.parse_args()
    if args.execute:
        execute_job(*args.execute)
        flush_logs()
    else:
        run()
