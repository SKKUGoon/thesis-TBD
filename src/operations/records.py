"""Closed-week replay of actual item and patient aggregates, without invented inventory."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path

from src.operations.contracts import FEATURES, Conflict, at, days
from src.operations.db import one, rows


@lru_cache(maxsize=1)
def load_records() -> dict:
    """Read the locally registered immutable aggregate snapshot; never download data."""
    path=Path('/records/cheonan-records-v1.json')
    if not path.is_file():raise Conflict('RECORDS_DATASET_NOT_REGISTERED')
    value=json.loads(path.read_text())
    if value['dataset_id']!='cheonan-records-v1' or {f['feature_key'] for f in FEATURES if f['kind']=='patient'}!=set(value['patient_keys']):
        raise Conflict('RECORDS_CONTRACT_MISMATCH')
    return value


def validate_records_window(start: date, end: date) -> None:
    """Require every calendar date while preserving missing individual quantities."""
    source=load_records()
    if any(str(day) not in source['days'] for day in days(start,end)):
        raise Conflict('RECORDED_PERIOD_UNAVAILABLE')


def make_recorded_delivery(conn: object, scope: str, start: date, end: date) -> dict:
    """Copy source quantities and observation masks for this closed interval only."""
    source=load_records();validate_records_window(start,end)
    state=one(conn,'SELECT * FROM app.scope WHERE id=%s',(scope,))
    items=rows(conn,'SELECT * FROM app.item ORDER BY code')
    lookup={item['code']:item for item in items};available=at(end)-timedelta(minutes=30)
    usage=[];patients=[]
    for day in days(start,end):
        record=source['days'][str(day)]
        for meta,value in zip(source['items'],record['usage']):
            item=lookup[meta['channel']]
            usage.append({'item_id':str(item['id']),'day':str(day),'quantity':str(value) if value is not None else None,'observation':'observed' if value is not None else 'missing','unit':item['base_unit'],'available_at':available.isoformat()})
        for key,value in zip(source['patient_keys'],record['patients']):
            patients.append({'feature_key':key,'day':str(day),'value':value,'observation':'observed' if value is not None else 'missing','available_at':available.isoformat()})
    return {'schema_version':1,'week_start':str(start),'week_end_exclusive':str(end),'source_revision':1,'supersedes_delivery_id':None,
            'source_closed_at':(at(end)-timedelta(hours=1)).isoformat(),'source_available_at':available.isoformat(),'is_complete':True,
            'model_version_id':str(state['model_version_id']),'usage':usage,'patients':patients,'inventory':None,'open_orders':[]}


def advance_recorded_week(conn: object, scope: str, job: dict) -> dict:
    """Advance the recorded cutoff and ingest actual usage, without simulating consumption."""
    from src.operations.synthetic import store_delivery
    state=one(conn,'SELECT * FROM app.scope WHERE id=%s',(scope,));start=state['active_week_start'];end=start+timedelta(days=7)
    if start!=state['next_week_start']:raise Conflict('STALE_WEEK')
    validate_records_window(start,end)
    conn.execute('UPDATE app.scope SET simulation_at=%s WHERE id=%s',(at(end)-timedelta(minutes=30),scope))
    principal=str(one(conn,"SELECT principal_id FROM app.membership WHERE role='test_operator' LIMIT 1")['principal_id'])
    delivery,ingest=store_delivery(conn,scope,make_recorded_delivery(conn,scope,start,end),principal)
    return {'delivery_id':delivery,'child_job_ids':[ingest]}
