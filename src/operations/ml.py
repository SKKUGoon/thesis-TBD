"""Real TimesFM inference from sealed DB snapshots, without a long DB transaction."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import time

import numpy as np

from src.operations.contracts import Conflict, days
from src.operations.db import audit, enqueue, insert, one, rows, transaction
from src.operations.inference import CHECKPOINT_HASHES, CHECKPOINT_REVISION, load_verified_model
from src.operations.ml_fence import assert_fence


def forecast(scope: str, job: dict, token: str) -> dict:
    """Load actual fixed weights on CPU, predict cohorts separately and commit once."""
    started = time.monotonic()
    with transaction(scope, lock=False) as conn:
        snapshot = one(conn, "SELECT * FROM app.input_snapshot WHERE id=%s AND status='sealed'", (job["resource_id"],))
        run = one(conn, "SELECT * FROM app.forecast_run WHERE job_id=%s", (job["id"],))
        if run["validity"] != "current":
            conn.execute("UPDATE app.forecast_run SET status='failed' WHERE id=%s", (run["id"],))
            conn.execute("UPDATE app.job SET status='blocked',error_code='INPUT_REVISED',updated_at=now() WHERE id=%s", (job["id"],))
            conn.execute("UPDATE app.job_attempt SET status='failed',finished_at=now(),error_code='INPUT_REVISED' WHERE job_id=%s AND fencing_token=%s", (job["id"], token))
            return {"forecast_id": str(run["id"])}
        items = rows(conn, "SELECT m.*,i.base_unit FROM app.model_item m JOIN app.item i ON i.id=m.item_id WHERE model_version_id=%s ORDER BY cohort,ordinal", (snapshot["model_version_id"],))
        features = rows(conn, "SELECT * FROM app.model_feature WHERE model_version_id=%s ORDER BY kind,ordinal", (snapshot["model_version_id"],))
        target_rows = rows(conn, "SELECT * FROM app.snapshot_target WHERE snapshot_id=%s", (snapshot["id"],))
        feature_rows = rows(conn, "SELECT * FROM app.snapshot_feature WHERE snapshot_id=%s", (snapshot["id"],))
    targets = {(str(r["item_id"]), r["day"]): float(r["quantity"]) if r["quantity"] is not None else np.nan for r in target_rows}
    values = {(r["kind"], r["feature_key"], r["day"]): float(r["value"]) if r["value"] is not None else np.nan for r in feature_rows}
    patient_keys = [r["feature_key"] for r in features if r["kind"] == "patient"]
    known_keys = [r["feature_key"] for r in features if r["kind"] == "known"]
    adapter = load_verified_model(Path("/model"))
    predictions = []
    origin = snapshot["forecast_origin"]
    for cohort, context in (("drug", 112), ("material", 56)):
        cohort_items = [i for i in items if i["cohort"] == cohort]
        past = days(origin-timedelta(days=context), origin)
        full = days(origin-timedelta(days=context), origin+timedelta(days=14))
        target = np.array([[[targets[(str(item["item_id"]), day)] for day in past] for item in cohort_items]], dtype="float32")
        patients = np.array([[[values.get(("patient", key, day), np.nan) for day in past] for key in patient_keys]], dtype="float32")
        known = np.array([[[values[("known", key, day)] for day in full] for key in known_keys]], dtype="float32")
        output = adapter.predict_masked(target, patients, known, 28)
        for i, item in enumerate(cohort_items):
            for horizon in range(14):
                quantiles = [round(float(v), 6) for v in output[0, i, horizon]]
                predictions.append({"item_id": item["item_id"], "target_day": origin+timedelta(days=horizon),
                                    "horizon_day": horizon+1, "prediction_raw": quantiles[4],
                                    "prediction": max(0, quantiles[4]), "quantiles": quantiles})
    with transaction(scope) as conn:
        assert_fence(conn, str(job["id"]), token)
        current = one(conn, "SELECT * FROM app.forecast_run WHERE id=%s", (run["id"],))
        for prediction in predictions:
            insert(conn, "forecast_value", scope, forecast_id=run["id"], model_version_id=snapshot["model_version_id"], **prediction)
        from psycopg.types.json import Jsonb

        conn.execute("UPDATE app.forecast_run SET status='succeeded',generated_at=now(),manifest=%s WHERE id=%s", (Jsonb({"checkpoint_revision": CHECKPOINT_REVISION, "checkpoint_hashes": CHECKPOINT_HASHES, "seconds": time.monotonic()-started, "device": "cpu", "target_contexts": [112, 56], "patient_context": 28, "fixed_weights": True}), run["id"]))
        child = enqueue(conn, scope, "proposal", str(run["id"]), str(job["id"])) if current["validity"] == "current" else None
        audit(conn, scope, "forecast.generated", str(run["id"]), {"stored_values": len(predictions)}, job=str(job["id"]))
        result = {"forecast_id": str(run["id"]), "child_job_ids": [child] if child else []}
        conn.execute("UPDATE app.job SET status='succeeded',result=%s,updated_at=now() WHERE id=%s", (Jsonb(result), job["id"]))
        conn.execute("UPDATE app.job_attempt SET status='succeeded',finished_at=now() WHERE job_id=%s AND fencing_token=%s", (job["id"], token))
    return result
