"""Lightweight attempt fencing shared with non-ML workers."""

from __future__ import annotations

from datetime import datetime, timezone

from src.operations.contracts import Conflict
from src.operations.db import one


def assert_fence(conn: object, job_id: str, token: str) -> None:
    current = one(conn, "SELECT a.*,j.status AS job_status FROM app.job_attempt a JOIN app.job j ON j.id=a.job_id WHERE a.job_id=%s ORDER BY a.number DESC LIMIT 1", (job_id,))
    if str(current["fencing_token"]) != token or current["status"] != "running" or current["job_status"] != "running" or current["lease_until"] <= datetime.now(timezone.utc):
        raise Conflict("STALE_ATTEMPT")
