"""Exercise running Linux services with real TimesFM; write reproducible synthetic evidence."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from uuid import uuid4


class Client:
    """Minimal API client that never prints authentication credentials."""

    def __init__(self, base: str, token: str, scope: str | None = None) -> None:
        self.base, self.token, self.scope = base, token, scope

    def request(self, path: str, body: dict | None = None, key: str | None = None) -> tuple[int, dict]:
        headers = {"Authorization": "Bearer "+self.token, "Content-Type": "application/json"}
        if self.scope:
            headers["X-Workspace-ID"] = self.scope
        if body is not None:
            headers["Idempotency-Key"] = key or str(uuid4())
        request = urllib.request.Request(self.base+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def get(self, path: str) -> dict:
        status, body = self.request(path)
        assert status == 200, (path, status, body)
        return body

    def post(self, path: str, body: dict, status: int = 202, key: str | None = None) -> dict:
        actual, result = self.request(path, body, key)
        assert actual == status, (path, actual, result)
        return result

    def all(self, path: str) -> list[dict]:
        result, cursor = [], None
        while True:
            page = self.get(path+("&" if "?" in path else "?")+"limit=100"+("&cursor="+cursor if cursor else ""))
            result.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return result

    def wait_ready(self, timeout: int = 180) -> dict:
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            scenario = self.get("/test/scenarios/"+self.scope)
            if scenario["status"] == "ready":
                return scenario
            if scenario["status"] == "failed":
                raise AssertionError({"scenario": scenario, "jobs": self.all("/jobs"), "logs": self.all("/logs")[-10:]})
            time.sleep(.5)
        raise TimeoutError({"jobs": self.all("/jobs"), "logs": self.all("/logs")[-10:]})

    def wait_job(self, job: str) -> dict:
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            value = self.get("/jobs/"+job)
            if value["status"] == "succeeded":
                return value
            if value["status"] in ("failed", "blocked"):
                raise AssertionError(value)
            time.sleep(.2)
        raise TimeoutError(job)


def run(base: str, token: str, output: Path, weeks: int) -> dict:
    started = time.monotonic()
    evidence = {"synthetic_only": True, "actual_timesfm": True, "status": "running", "checks": [], "weeks": []}
    def passed(name: str) -> None:
        evidence["checks"].append(name)
        print(name, flush=True)
    try:
        admin = Client(base, token)
        assert admin.get("/health/ready")["status"] == "ok"
        assert Client(base, "invalid").request("/items")[0] == 401
        passed("readiness_and_authentication")
        create = {"name": "실제 TimesFM 통합 검사", "seed": 20261021, "hospital_code": "SYNTHETIC", "warehouse_code": "MAIN",
                  "first_week_start": "2024-03-04", "initial_history_days": 112, "profile": "timesfm30_v1"}
        key = str(uuid4())
        created = admin.post("/test/scenarios", create, key=key)
        assert admin.post("/test/scenarios", create, key=key) == created
        assert admin.request("/test/scenarios", {**create, "seed": 7}, key=key)[0] == 409
        client = Client(base, token, created["scenario_id"])
        evidence["scope_id"] = client.scope
        client.wait_ready()
        assert len(client.all("/items")) == 30
        model = client.get("/test/scenarios/"+client.scope)["model_version_id"]
        contract = client.get("/model-versions/"+model+"/input-contract")
        assert len(contract["items"]) == 30 and len(contract["features"]) == 85
        passed("bootstrap_112_days_30_items_and_idempotency")
        for week in range(weeks):
            scenario = client.get("/test/scenarios/"+client.scope)
            advance = {"expected_version": scenario["version"], "week_start": scenario["next_week_start"]}
            key = str(uuid4())
            received = client.post("/test/scenarios/"+client.scope+"/weekly-deliveries", advance, key=key)
            assert client.post("/test/scenarios/"+client.scope+"/weekly-deliveries", advance, key=key) == received
            assert client.request("/test/scenarios/"+client.scope+"/weekly-deliveries", advance)[0] == 409
            done = client.wait_ready()
            assert done["version"] == scenario["version"]+1
            forecast = max(client.all("/forecasts"), key=lambda f: f["forecast_origin"])
            assert forecast["status"] == "succeeded" and forecast["stored_values"] == 420
            values = client.all("/forecasts/"+forecast["id"]+"/values")
            assert len(values) == 420 and all(len(v["quantiles"]) == 9 and Decimal(v["prediction"]) >= 0 for v in values)
            proposals = [p for p in client.all("/order-proposals") if p["forecast_id"] == forecast["id"]]
            proposal = max(proposals, key=lambda p: p["version"])
            assert proposal["status"] == "pending", proposal["blocking_codes"]
            decision_body = {"proposal_version": proposal["version"], "content_hash": proposal["content_hash"], "decision": "approve"}
            if week == 0:
                assert client.request("/order-proposals/"+proposal["id"]+"/decisions", {**decision_body, "content_hash": "0"*64})[0] == 409
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: client.request("/order-proposals/"+proposal["id"]+"/decisions", decision_body), range(2)))
                assert sorted(status for status, _ in results) == [200, 409], results
                decision = next(body for status, body in results if status == 200)
                passed("concurrent_approval_exactly_one_order_and_hash_guard")
            else:
                decision = client.post("/order-proposals/"+proposal["id"]+"/decisions", decision_body, 200)
            orders = []
            for order_id in decision["order_ids"]:
                deadline = time.monotonic()+30
                while time.monotonic() < deadline:
                    order = client.get("/purchase-orders/"+order_id)
                    if order["status"] == "accepted":
                        break
                    time.sleep(.2)
                assert order["status"] == "accepted", order
                orders.append(order)
            assert orders
            if week == 0:
                order = orders[0]
                before = {i["item_id"]: Decimal(i["on_hand"]) for i in client.all("/inventory")}
                first = order["lines"][0]
                partial = {"source_receipt_key": "manual-partial", "occurred_at": order["accepted_at"],
                           "lines": [{"order_line_id": first["id"], "quantity": str(Decimal(first["ordered_units"])/2), "unit": first["unit"]}]}
                accepted_receipt = client.post("/purchase-orders/"+order["id"]+"/receipts", partial)
                client.wait_job(accepted_receipt["job_id"])
                assert client.post("/purchase-orders/"+order["id"]+"/receipts", partial)["resource_id"] == accepted_receipt["resource_id"]
                assert client.get("/purchase-orders/"+order["id"])["receipt_status"] == "partially_received"
                assert client.request("/purchase-orders/"+order["id"]+"/receipts", {**partial, "source_receipt_key": "over-receipt", "lines": [{**partial["lines"][0], "quantity": "999999"}]})[0] == 409
                order = client.get("/purchase-orders/"+order["id"])
                full = {"source_receipt_key": "manual-remaining", "occurred_at": order["accepted_at"],
                        "lines": [{"order_line_id": l["id"], "quantity": l["remaining_units"], "unit": l["unit"]} for l in order["lines"] if Decimal(l["remaining_units"]) > 0]}
                final = client.post("/purchase-orders/"+order["id"]+"/receipts", full)
                client.wait_job(final["job_id"])
                completed_order = client.get("/purchase-orders/"+order["id"])
                assert completed_order["receipt_status"] == "received" and all(Decimal(l["remaining_units"]) == 0 for l in completed_order["lines"])
                after = {i["item_id"]: Decimal(i["on_hand"]) for i in client.all("/inventory")}
                for line in completed_order["lines"]:
                    assert after[line["item_id"]]-before[line["item_id"]] == Decimal(line["ordered_units"])
                passed("partial_full_duplicate_excess_receipts_and_stock_balance")
            outcomes = client.all("/test/scenarios/"+client.scope+"/demand-outcomes")
            assert len(outcomes) == 30*7*(week+1)
            assert all(Decimal(r["requested_units"]) == Decimal(r["fulfilled_units"])+Decimal(r["lost_units"]) for r in outcomes)
            evidence["weeks"].append({"week_start": advance["week_start"], "forecast_id": forecast["id"], "values": len(values), "proposal_id": proposal["id"], "order_ids": decision["order_ids"], "scope_version": done["version"]})
            passed(f"week_{week+1}_real_timesfm_420_values_approval_supplier")
        other = admin.post("/test/scenarios", {**create, "name": "No 검증", "profile": "smoke_v1"})
        no = Client(base, token, other["scenario_id"])
        no.wait_ready()
        no.post("/test/scenarios/"+no.scope+"/weekly-deliveries", {"expected_version": 1, "week_start": "2024-03-04"})
        no.wait_ready()
        proposal = no.all("/order-proposals")[0]
        rejected = no.post("/order-proposals/"+proposal["id"]+"/decisions", {"proposal_version": proposal["version"], "content_hash": proposal["content_hash"], "decision": "reject"}, 200)
        assert rejected["order_ids"] == [] and no.all("/purchase-orders") == []
        assert no.request("/purchase-orders/"+evidence["weeks"][0]["order_ids"][0])[0] == 404
        passed("no_decision_creates_no_order_and_cross_scope_access_denied")
        services = {row["service"] for row in client.all("/logs")}
        assert {"business", "ingest", "ml"} <= services, services
        assert all(j["status"] == "succeeded" for j in client.all("/jobs"))
        passed("queue_logger_same_database_and_all_jobs_succeeded")
        evidence["status"] = "succeeded"
    except Exception as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        evidence["elapsed_seconds"] = time.monotonic()-started
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8088/api/v1")
    parser.add_argument("--token-file", type=Path, default=Path("deploy/.state/test-token"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--weeks", type=int, default=6)
    args = parser.parse_args()
    run(args.base, args.token_file.read_text().strip(), args.output, args.weeks)
