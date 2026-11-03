"""Restore a real PG dump in an isolated DB and reconcile against the same supplier key."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from fault_scenario import ROOT, compose, sql


def run(output: Path, order_id: str) -> dict:
    """Leave a private backup and evidence, then remove only the temporary restored database."""
    started = time.monotonic()
    restored = "restore_validation_"+uuid4().hex[:12]
    evidence = {"synthetic_only": True, "status": "running", "checks": []}
    backup = output.parent / "operations.backup"
    try:
        with backup.open("wb") as destination:
            subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "exec", "-T", "db", "pg_dump", "-Fc", "-U", "migration", "operations"], cwd=ROOT, stdout=destination, check=True)
        compose("exec", "-T", "db", "createdb", "-U", "migration", restored)
        with backup.open("rb") as source:
            result = subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "exec", "-T", "db", "pg_restore", "-U", "migration", "-d", restored], cwd=ROOT, stdin=source, capture_output=True)
        assert result.returncode == 0, result.stderr.decode()[-2000:]
        original = json.loads(sql(f"SELECT row_to_json(o) FROM app.purchase_order o WHERE id='{order_id}'"))
        restored_order = json.loads(compose("exec", "-T", "db", "psql", "-U", "migration", "-d", restored, "-Atc", f"SELECT row_to_json(o) FROM app.purchase_order o WHERE id='{order_id}'"))
        assert restored_order == original
        evidence["checks"].append("custom_pg_dump_restored_order_key_hash_and_history")
        # The synthetic supplier still points at its original durable acceptance ledger.
        # Only the restored application's acknowledgement is set to unknown.
        script = '''import os
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from src.operations.db import transaction,enqueue,insert,one
from src.operations.worker import execute_job
os.environ['DATABASE_URL']=os.environ['DATABASE_URL'].rsplit('/',1)[0]+'/{database}'
scope='{scope}'
with transaction(scope) as c:
 c.execute("UPDATE app.purchase_order SET status='submission_unknown' WHERE id=%s",('{order}',))
 job=enqueue(c,scope,'reconcile','{order}',key='restore:'+str(uuid4()))
 token=str(uuid4());now=datetime.now(timezone.utc)
 c.execute("UPDATE app.job SET status='running',current_attempt=1 WHERE id=%s",(job,))
 insert(c,'job_attempt',scope,job_id=job,number=1,fencing_token=token,status='running',started_at=now,heartbeat_at=now,lease_until=now+timedelta(seconds=180))
execute_job(scope,job,token)
with transaction(scope) as c:
 assert one(c,'SELECT status FROM app.purchase_order WHERE id=%s',('{order}',))['status']=='accepted'
 assert one(c,'SELECT status FROM app.job WHERE id=%s',(job,))['status']=='succeeded'
 assert one(c,"SELECT kind FROM app.order_attempt WHERE order_id=%s ORDER BY started_at DESC LIMIT 1",('{order}',))['kind']=='lookup'
print('RESTORED_KEY_LOOKUP_CONFIRMED')
'''.format(database=restored, scope=original["scope_id"], order=order_id)
        before = sql(f"SELECT count(*) FROM supplier_sim.acceptance WHERE order_key='{original['order_key']}'")
        assert "RESTORED_KEY_LOOKUP_CONFIRMED" in compose("exec", "-T", "business", "python", "-c", script)
        assert sql(f"SELECT count(*) FROM supplier_sim.acceptance WHERE order_key='{original['order_key']}'") == before == "1"
        evidence["checks"].append("restored_unknown_order_lookup_same_live_supplier_key_without_duplicate")
        evidence["status"] = "succeeded"
    except Exception as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        compose("exec", "-T", "db", "dropdb", "-U", "migration", "--if-exists", restored)
        evidence["elapsed_seconds"] = time.monotonic()-started
        output.write_text(json.dumps(evidence, indent=2)+"\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--order-id", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.order_id)))
