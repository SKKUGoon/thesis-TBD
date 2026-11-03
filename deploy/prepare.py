"""Prepare private evaluation credentials and a portable, verified model directory."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import secrets
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
ROLES = {
    "api": "scope principal membership artifact delivery job outbox api_idempotency proposal decision purchase_order purchase_order_line order_reservation receipt receipt_line scenario_creation_request business_event ordering_policy",
    "ingest": "delivery validation_issue usage_day patient_day inventory_anchor inventory_anchor_line external_open_order inventory_state input_snapshot snapshot_source snapshot_target snapshot_feature forecast_run job outbox inbox business_event delivery_impact scope proposal",
    "ml": "forecast_run forecast_value job job_attempt outbox inbox business_event",
    "business": "scope membership artifact supplier item ordering_policy model_version model_item model_feature delivery inventory_state inventory_event proposal proposal_line purchase_order purchase_order_line order_reservation order_attempt receipt receipt_line business_event demand_outcome job outbox inbox forecast_evaluation evaluation_delivery",
    "relay": "job job_attempt outbox scope business_event",
    "logger": "app_log",
}
READ_TABLES = {
    "ml": "scope artifact item model_version model_item model_feature input_snapshot snapshot_source snapshot_target snapshot_feature job job_attempt outbox inbox forecast_run forecast_value business_event",
    "relay": "scope job job_attempt outbox business_event",
    "logger": "app_log",
}


def prepare(checkpoint: Path) -> None:
    state = ROOT / ".state"
    state.mkdir(exist_ok=True, mode=0o700)
    model = state / "model"
    model.mkdir(exist_ok=True)
    hashes = {
        "config.json": "ff17bbc07b792c5a904cca265b8468579d736a4fe84981da25eb871b0a125bc6",
        "model.safetensors": "a7592b0a8432baee54483254e5647856911ce69e09d09a9bb65904b2d98f17da",
    }
    for name, expected in hashes.items():
        destination = model / name
        if not destination.exists():
            shutil.copyfile(checkpoint / name, destination)
        with destination.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"CHECKPOINT_HASH_MISMATCH:{name}")
        destination.chmod(0o444)
    values = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().splitlines() if "=" in line) if (ROOT / ".env").exists() else {
        "POSTGRES_PASSWORD": secrets.token_hex(24), "BROKER_PASSWORD": secrets.token_hex(24),
        "JWT_SECRET": secrets.token_hex(32), "API_PORT": "8088"}
    values["MODEL_DIR"] = str(model)
    project = ROOT.parent
    source_hash = hashlib.sha256()
    for source in sorted((project / "src").rglob("*.py")):
        source_hash.update(str(source.relative_to(project)).encode()+source.read_bytes())
    values["APP_SOURCE_SHA256"] = source_hash.hexdigest()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, text=True, capture_output=True)
    values["APP_CODE_COMMIT"] = commit.stdout.strip() if commit.returncode == 0 else "bundle-source-manifest"
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=project, text=True, capture_output=True)
    values["APP_CODE_DIRTY"] = "1" if dirty.returncode != 0 or dirty.stdout.strip() else "0"
    image = subprocess.run(["docker", "image", "inspect", "paper-operations-ml:eval-v1", "--format", "{{.Id}}"], text=True, capture_output=True)
    values["ML_IMAGE_DIGEST"] = image.stdout.strip() if image.returncode == 0 else "unregistered"
    sql = []
    for role, tables in ROLES.items():
        password = values.get(f"DB_{role.upper()}_PASSWORD", secrets.token_hex(24))
        values[f"DB_{role.upper()}_PASSWORD"] = password
        sql.extend([f"CREATE ROLE op_{role} LOGIN PASSWORD '{password}';",
                    f"GRANT USAGE ON SCHEMA app TO op_{role};"])
        if role in READ_TABLES:
            sql.append(f"GRANT SELECT ON {','.join('app.'+t for t in READ_TABLES[role].split())} TO op_{role};")
        else:
            sql.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA app TO op_{role};")
        for table in tables.split():
            sql.append(f"GRANT INSERT, UPDATE ON app.{table} TO op_{role};")
        if role in ("relay", "business", "logger"):
            sql.append(f"GRANT EXECUTE ON FUNCTION app.list_scope_ids() TO op_{role};")
    # Worker execution records are common to ingestion, model and business roles.
    sql.append("GRANT INSERT, UPDATE ON app.job_attempt TO op_ingest, op_business;")
    sql.append("GRANT DELETE ON app.app_log TO op_logger;")
    sql.append("GRANT INSERT ON app.artifact_payload TO op_api, op_business;")
    supplier_password = values.get("DB_SUPPLIER_PASSWORD", secrets.token_hex(24))
    values["DB_SUPPLIER_PASSWORD"] = supplier_password
    sql.extend([f"CREATE ROLE op_supplier LOGIN PASSWORD '{supplier_password}';",
                "GRANT USAGE ON SCHEMA supplier_sim TO op_supplier;",
                "GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA supplier_sim TO op_supplier;",
                "INSERT INTO app.principal(subject,kind,platform_role) VALUES ('local-test-admin','human','test_admin');"])
    (state / "roles.sql").write_text("\n".join(sql) + "\n")
    (state / "roles.sql").chmod(0o644)  # Parent is private; postgres must read during initialization.
    env = ROOT / ".env"
    env.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    env.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    prepare(parser.parse_args().checkpoint)
