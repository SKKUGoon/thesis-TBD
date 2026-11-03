"""Independent durable fake supplier; internal order state is not acceptance proof."""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import FastAPI, HTTPException
from psycopg.types.json import Jsonb

from src.operations.db import plain, transaction
from src.operations.messaging import log

app = FastAPI(title="Private synthetic supplier")


@app.put("/orders/{order_key}")
def accept(order_key: UUID, body: dict) -> dict:
    """Accept the same key/content once, optionally lose the first response after commit."""
    lost = False
    with transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(order_key),))
        row = conn.execute("SELECT * FROM supplier_sim.acceptance WHERE order_key=%s", (order_key,)).fetchone()
        if row is None:
            lost = os.getenv("SUPPLIER_LOSE_FIRST_RESPONSE", "0") == "1"
            row = conn.execute("INSERT INTO supplier_sim.acceptance(order_key,scope_id,content_hash,supplier_ref,body,accepted_at,fault_response_lost) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *", (order_key, body["scope_id"], body["content_hash"], f"SYN-{order_key}", Jsonb(body), body["accepted_at"], lost)).fetchone()
        elif row["content_hash"] != body["content_hash"]:
            raise HTTPException(409, "ORDER_KEY_CONFLICT")
    if lost:
        log(str(row["scope_id"]), "supplier", "RESPONSE_LOST", "Synthetic acknowledgement intentionally lost")
        raise HTTPException(503, "SIMULATED_RESPONSE_LOST_AFTER_COMMIT")
    log(str(row["scope_id"]), "supplier", "SUPPLIER_ACCEPTED", "Synthetic supplier acceptance committed")
    return plain(row)


@app.get("/orders/{order_key}")
def lookup(order_key: UUID) -> dict:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM supplier_sim.acceptance WHERE order_key=%s", (order_key,)).fetchone()
        if row is None:
            raise HTTPException(404, "NOT_FOUND")
        log(str(row["scope_id"]), "supplier", "SUPPLIER_LOOKUP", "Durable supplier key found")
        return plain(row)
