"""Evaluate delayed observed truth; retain revision-specific evaluation evidence."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.operations.db import digest, insert, one, plain, rows


def evaluate(conn: object, scope: str, job: dict) -> dict:
    now = one(conn, "SELECT simulation_at FROM app.scope WHERE id=%s", (scope,))["simulation_at"]
    for forecast in rows(conn, "SELECT f.*,s.forecast_origin FROM app.forecast_run f JOIN app.input_snapshot s ON s.id=f.snapshot_id WHERE f.status='succeeded'"):
        for item in rows(conn, "SELECT DISTINCT item_id FROM app.forecast_value WHERE forecast_id=%s", (forecast["id"],)):
            for offset, window in ((0, "week1"), (7, "week2")):
                start = forecast["forecast_origin"]+timedelta(days=offset)
                end = start+timedelta(days=7)
                truth = rows(conn, """SELECT DISTINCT ON(u.day) u.* FROM app.usage_day u JOIN app.delivery d ON d.id=u.delivery_id
                    WHERE d.status='validated' AND u.item_id=%s AND u.day>=%s AND u.day<%s AND u.available_at<=%s
                    ORDER BY u.day,d.source_revision DESC,d.received_at DESC""", (item["item_id"], start, end, now))
                truth_hash = digest(plain(truth))
                if conn.execute("SELECT 1 FROM app.forecast_evaluation WHERE forecast_id=%s AND item_id=%s AND evaluation_window=%s AND truth_hash=%s", (forecast["id"], item["item_id"], window, truth_hash)).fetchone():
                    continue
                observed = {r["day"]: r for r in truth if r["observation"] == "observed"}
                predictions = rows(conn, "SELECT * FROM app.forecast_value WHERE forecast_id=%s AND item_id=%s AND target_day>=%s AND target_day<%s", (forecast["id"], item["item_id"], start, end))
                errors = [p["prediction"]-observed[p["target_day"]]["quantity"] for p in predictions if p["target_day"] in observed]
                status = "ready" if len(observed) == 7 else "pending" if now.date() < end else "unavailable"
                evaluation = insert(conn, "forecast_evaluation", scope, forecast_id=forecast["id"], item_id=item["item_id"], evaluation_window=window,
                                    truth_hash=truth_hash, status=status, observations=len(observed), mae=sum(map(abs, errors), Decimal(0))/7 if status == "ready" else None,
                                    bias=sum(errors, Decimal(0))/7 if status == "ready" else None, reason_code=None if status == "ready" else "LABEL_WINDOW_OPEN" if status == "pending" else "MISSING_TRUTH",
                                    evaluated_at=now)
                for delivery in {r["delivery_id"] for r in truth}:
                    insert(conn, "evaluation_delivery", scope, evaluation_id=evaluation, delivery_id=delivery)
    return {}
