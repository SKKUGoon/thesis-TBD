"""Regression checks for deployed API filters, signed cursors, policies and roles."""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from uuid import uuid4

from fault_scenario import compose, sql, wait_status
from system_scenario import Client


def run(base: str, token: str, scope: str, output: Path) -> dict:
    evidence = {"status": "running", "checks": []}
    def passed(name: str) -> None:
        evidence["checks"].append(name)
        print(name, flush=True)
    try:
        client = Client(base, token, scope)
        assert len(client.all("/items?kind=drug")) == 20
        assert len(client.all("/items?kind=material")) == 10
        assert client.request("/items?kind=unknown")[0] == 422
        first = client.get("/items?kind=drug&limit=2")
        cursor = first["next_cursor"]
        second = client.get("/items?"+urlencode({"kind": "drug", "limit": 2, "cursor": cursor}))
        assert not ({i["id"] for i in first["items"]} & {i["id"] for i in second["items"]})
        assert client.request("/items?"+urlencode({"kind": "material", "cursor": cursor}))[0] == 400
        assert client.request("/items?cursor=malformed")[0] == 400
        assert client.request("/items?limit=0")[0] == 422
        passed("filters_and_signed_scope_path_filter_pagination")
        forecast = max(client.all("/forecasts"), key=lambda f: f["forecast_origin"])
        item = client.all("/items")[0]
        values = client.all("/forecasts/"+forecast["id"]+"/values?item_id="+item["id"])
        assert len(values) == 14 and len({v["item_id"] for v in values}) == 1
        assert values == sorted(values, key=lambda v: (v["target_day"], v["item_id"]))
        limited = client.all("/forecasts/"+forecast["id"]+"/values?"+urlencode({"item_id": item["id"], "date_from": values[2]["target_day"], "date_to": values[4]["target_day"]}))
        assert len(limited) == 3
        assert all(j["stage"] == "forecast" for j in client.all("/jobs?stage=forecast"))
        evaluated = min(client.all("/forecasts"), key=lambda f: f["forecast_origin"])
        assert client.all("/forecasts/"+evaluated["id"]+"/evaluations?window=week1")
        passed("forecast_item_inclusive_date_filters_order_and_evaluation_route")
        # A viewer may read same-scope business data but cannot approve or operate tests.
        subject = "synthetic-viewer-"+uuid4().hex
        principal = sql(f"INSERT INTO app.principal(subject,kind) VALUES('{subject}','human') RETURNING id").splitlines()[0]
        sql(f"INSERT INTO app.membership(scope_id,principal_id,role) VALUES('{scope}','{principal}','viewer')")
        script = "import jwt,os,time;print(jwt.encode({'sub':'"+subject+"','iat':int(time.time()),'exp':int(time.time())+600,'iss':'paper-operations','aud':'paper-api'},os.environ['JWT_SECRET'],algorithm='HS256'))"
        restricted_token = compose("exec", "-T", "api", "python", "-c", script)
        viewer = Client(base, restricted_token, scope)
        assert viewer.get("/items")["items"]
        assert viewer.request("/test/scenarios/"+scope)[0] == 403
        assert viewer.request("/health/ready")[0] == 403
        existing = client.all("/order-proposals")[0]
        assert viewer.request("/order-proposals/"+existing["id"]+"/decisions", {"proposal_version": existing["version"], "content_hash": existing["content_hash"], "decision": "reject"})[0] == 403
        passed("viewer_read_approver_test_operator_and_platform_role_boundaries")
        # Recalculate with existing accepted supply; zero safety yields zero additional orders.
        policy_request_keys = ["supplier_id", "lead_days", "review_days", "pack_size", "minimum_packs", "safety_units", "valid_from", "calendar_rule"]
        for item in client.all("/items"):
            policy = max(client.all("/items/"+item["id"]+"/ordering-policies"), key=lambda p: p["version"])
            body = {key: policy[key] for key in policy_request_keys}
            body.update(expected_version=policy["version"], safety_units="0")
            client.post("/items/"+item["id"]+"/ordering-policies", body, 201)
        old = max((p for p in client.all("/order-proposals") if p["forecast_id"] == forecast["id"]), key=lambda p: p["version"])
        recalculated = client.post("/order-proposals/"+old["id"]+"/recalculate", {"expected_version": old["version"], "reason": "기존 입고 예정 반영"})
        result = client.wait_job(recalculated["job_id"])
        proposal = client.get("/order-proposals/"+result["result"]["proposal_id"])
        assert all(line["order_units"] in ("0", "0.000000") for line in proposal["lines"])
        decision = client.post("/order-proposals/"+proposal["id"]+"/decisions", {"proposal_version": proposal["version"], "content_hash": proposal["content_hash"], "decision": "approve"}, 200)
        assert decision["dispatch_status"] == "not_required" and decision["order_ids"] == []
        passed("policy_versions_recalculation_approved_supply_not_double_counted_zero_order")
        # Hold dispatch, change policy, then verify a definitely unsent blocked order can be voided.
        item = client.all("/items")[0]
        policy = max(client.all("/items/"+item["id"]+"/ordering-policies"), key=lambda p: p["version"])
        body = {key: policy[key] for key in policy_request_keys}
        body.update(expected_version=policy["version"], safety_units="1000")
        updated_policy = client.post("/items/"+item["id"]+"/ordering-policies", body, 201)
        recalc = client.post("/order-proposals/"+proposal["id"]+"/recalculate", {"expected_version": proposal["version"], "reason": "전송 전 변경 차단 검사"})
        result = client.wait_job(recalc["job_id"])
        positive = client.get("/order-proposals/"+result["result"]["proposal_id"])
        compose("stop", "business")
        approved = client.post("/order-proposals/"+positive["id"]+"/decisions", {"proposal_version": positive["version"], "content_hash": positive["content_hash"], "decision": "approve"}, 200)
        order_id = approved["order_ids"][0]
        body.update(expected_version=updated_policy["version"], safety_units="1001")
        client.post("/items/"+item["id"]+"/ordering-policies", body, 201)
        compose("start", "business")
        order = wait_status(client, order_id, "blocked")
        released = client.post("/purchase-orders/"+order_id+"/release", {"expected_status": "blocked", "content_hash": order["content_hash"], "reason": "미전송 주문 종료"}, 200)
        assert released["status"] == "voided" and all(l["remaining_units"] in ("0", "0.000000") for l in released["lines"])
        assert sql(f"SELECT count(*) FROM app.order_attempt WHERE order_id='{order_id}' AND kind='submit'") == "0"
        passed("pre_dispatch_policy_change_blocks_and_safe_unsent_release")
        evidence["status"] = "succeeded"
    except Exception as error:
        evidence.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        compose("start", "business")
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8088/api/v1")
    parser.add_argument("--token-file", type=Path, default=Path("deploy/.state/test-token"))
    parser.add_argument("--scope", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.base, args.token_file.read_text().strip(), args.scope, args.output)
