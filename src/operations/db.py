"""Scoped PostgreSQL transactions, atomic commands and durable audit events."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from typing import Any, Iterator
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def serial(value: Any) -> Any:
    """Convert DB values to API JSON without losing quantity precision."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    raise TypeError(type(value).__name__)


def plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=serial))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def transaction(scope: str | None = None, lock: bool = True) -> Iterator[psycopg.Connection]:
    """Open a short RLS transaction and optionally serialize scope mutations."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        if scope:
            conn.execute("SELECT set_config('app.scope_id',%s,true)", (str(scope),))
            if lock:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(scope),))
        yield conn


def one(conn: psycopg.Connection, sql: str, params: tuple = ()) -> dict:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise LookupError("NOT_FOUND")
    return row


def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return conn.execute(sql, params).fetchall()


def insert(conn: psycopg.Connection, table: str, scope: str | None = None, **values: Any) -> str:
    """Insert internally named columns and return the generated UUID."""
    from psycopg import sql

    if scope:
        values = {"scope_id": scope, **values}
    statement = sql.SQL("INSERT INTO app.{} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, values)),
        sql.SQL(",").join(sql.Placeholder() for _ in values),
    )
    return str(conn.execute(statement, tuple(Jsonb(v) if isinstance(v, dict) else v for v in values.values())).fetchone()["id"])


def enqueue(conn: psycopg.Connection, scope: str, stage: str, resource: str,
            parent: str | None = None, key: str | None = None) -> str:
    """Persist a job and command outbox in the caller's business transaction."""
    job = insert(conn, "job", scope, stage=stage, resource_id=resource,
                 parent_job_id=parent, dedupe_key=key or f"{stage}:{resource}", status="queued")
    queue = "ml" if stage == "forecast" else "ingest" if stage == "ingest" else "business"
    insert(conn, "outbox", scope, job_id=job, message_id=str(uuid4()), attempt=1,
           message_type=f"command.{stage}", routing_key=queue,
           payload={"scope_id": str(scope), "job_id": job, "attempt": 1},
           status="pending", available_at=datetime.now().astimezone())
    return job


def audit(conn: psycopg.Connection, scope: str, kind: str, aggregate: str, data: dict,
          principal: str | None = None, job: str | None = None) -> None:
    sequence = one(conn, "SELECT coalesce(max(sequence),0)+1 AS n FROM app.business_event WHERE aggregate_id=%s", (aggregate,))["n"]
    insert(conn, "business_event", scope, aggregate_type=kind.split(".")[0],
           aggregate_id=aggregate, sequence=sequence, kind=kind, principal_id=principal,
           job_id=job, occurred_at=datetime.now().astimezone(), data=data)
