"""Two-container replay API with durable SQLite deadlines and request deduplication."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hmac
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.demo.seed import load_seed


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_date: date
    forecast_loading_seconds: StrictInt = Field(ge=0, le=600)


def shifted(day: str, count: int) -> str:
    return str(date.fromisoformat(day) + timedelta(days=count))


def stamp(day: str) -> str:
    return day + 'T00:00:00+09:00'


def uid(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, value))


class DemoStore:
    """Persist settings, scopes, job deadlines, idempotency and events atomically."""
    def __init__(self, seed_path: Path, config_path: Path, database_path: Path) -> None:
        self.seed = load_seed(seed_path)
        self.origins = sorted(self.seed['forecasts'])
        self.path = database_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items = [{'id': uid(i['channel']), 'code': i['channel'], 'name': i['name'], 'kind': i['cohort'], 'base_unit': i['unit'], 'target_semantics': 'drug_record_units' if i['cohort'] == 'drug' else 'material_issue_units'} for i in self.seed['items']]
        self.hospital = json.loads(config_path.read_text()).get('hospital_name', '천안의료원')
        config = Settings.model_validate({k: v for k, v in json.loads(config_path.read_text()).items() if k != 'hospital_name'}).model_dump(mode='json')
        self.validate_start(config['start_date'])
        with closing(sqlite3.connect(self.path, timeout=15)) as conn, conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS scopes(id TEXT PRIMARY KEY,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS requests(path TEXT NOT NULL,key TEXT NOT NULL,body TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(path,key)); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);')
            old = conn.execute("SELECT value FROM metadata WHERE key='seed_hash'").fetchone()
            if old and old[0] != self.seed['sha256']:
                raise ValueError('SEED_CHANGED_USE_NEW_STATE_VOLUME')
            if not old:
                conn.execute("INSERT INTO metadata VALUES ('seed_hash',?)", (self.seed['sha256'],))
                conn.execute("INSERT INTO metadata VALUES ('settings',?)", (json.dumps(config),))
                self.create_scope(conn, config['start_date'])

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=15)) as conn, conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            yield conn

    def validate_start(self, origin: str) -> None:
        if origin not in self.origins:
            raise ValueError('START_DATE_NOT_IN_SEED')

    def settings(self, conn: sqlite3.Connection) -> dict[str, Any]:
        return json.loads(conn.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0])

    def event(self, conn: sqlite3.Connection, kind: str, body: dict) -> None:
        conn.execute('INSERT INTO events(at,kind,body) VALUES (?,?,?)', (datetime.now(timezone.utc).isoformat(), kind, json.dumps(body)))

    def save(self, conn: sqlite3.Connection, state: dict) -> None:
        conn.execute('INSERT INTO scopes VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (state['id'], json.dumps(state)))

    def create_scope(self, conn: sqlite3.Connection, origin: str) -> dict:
        self.validate_start(origin)
        identifier = str(uuid4())
        state = {'id': identifier, 'name': self.hospital + ' 사용 기록', 'version': 1, 'origin': origin, 'origins': [o for o in self.origins if shifted(origin, -7) <= o <= origin], 'pending': None, 'jobs': [], 'created_at': datetime.now(timezone.utc).isoformat()}
        self.save(conn, state)
        self.event(conn, 'workspace.created', {'scope_id': identifier, 'origin': origin, 'mode': 'prerecorded_replay'})
        return state

    def get(self, conn: sqlite3.Connection, identifier: str) -> dict:
        row = conn.execute('SELECT body FROM scopes WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('WORKSPACE_NOT_FOUND')
        state = json.loads(row[0])
        pending = state['pending']
        if pending and time.time() >= pending['ready_at']:
            state['origin'] = pending['origin']
            state['origins'].append(pending['origin'])
            state['pending'] = None
            state['version'] += 1
            state['jobs'][-1]['status'] = 'succeeded'
            state['jobs'][-1]['updated_at'] = datetime.now(timezone.utc).isoformat()
            self.save(conn, state)
            self.event(conn, 'forecast.received', {'scope_id': identifier, 'origin': pending['origin'], 'mode': 'prerecorded_replay'})
        return state

    def scenario(self, state: dict) -> dict:
        return {'id': state['id'], 'name': state['name'], 'data_source': 'records', 'inventory_source': 'not_registered', 'status': 'advancing' if state['pending'] else 'ready', 'seed': 20261008, 'version': state['version'], 'simulation_at': stamp(state['origin']), 'next_week_start': state['origin'], 'model_version_id': uid('prerecorded-model'), 'active_week_start': state['origin'] if state['pending'] else None, 'last_completed_week_start': shifted(state['origin'], -7), 'last_job_id': state['jobs'][-1]['id'] if state['jobs'] else None, 'blocking_code': None}

    def forecast(self, state: dict, origin: str) -> dict:
        identifier = uid(state['id'] + origin)
        return {'id': identifier, 'snapshot_id': uid(identifier + 'snapshot'), 'model_version_id': uid('prerecorded-model'), 'forecast_origin': origin, 'input_cutoff_at': stamp(origin), 'knowledge_as_of': stamp(origin), 'generated_at': stamp(origin), 'status': 'succeeded', 'validity': 'current', 'job_id': uid(identifier + 'job'), 'horizon_days': 14, 'expected_values': 420, 'stored_values': 420}

    def find_origin(self, state: dict, forecast_id: str) -> str:
        for origin in state['origins']:
            if self.forecast(state, origin)['id'] == forecast_id:
                return origin
        raise KeyError('FORECAST_NOT_AVAILABLE')

    def delivery(self, state: dict, origin: str) -> dict:
        forecast = self.forecast(state, origin)
        return {'id': uid(forecast['id'] + 'delivery'), 'kind': 'weekly', 'week_start': shifted(origin, -7), 'week_end_exclusive': origin, 'source_revision': 1, 'source_closed_at': stamp(origin), 'source_available_at': stamp(origin), 'received_at': stamp(origin), 'status': 'validated', 'supersedes_delivery_id': None, 'job_id': forecast['job_id'], 'snapshot_id': forecast['snapshot_id'], 'artifact_hash': self.seed['sha256'], 'issue_count': 0, 'correction_impacts': []}

    def prediction_values(self, origin: str, model: str = 'timesfm') -> list[dict]:
        """Read saved predictions or repeat pre-origin weekdays; preserve missing input."""
        values = []
        indices = {item['code']: i for i, item in enumerate(self.items)}
        for record in self.seed['forecasts'][origin]:
            value = dict(record)
            if model == 'naive' and self.items[indices[value['channel']]]['kind'] == 'drug':
                offset = (date.fromisoformat(value['target_day']) - date.fromisoformat(origin)).days
                source = shifted(origin, -7 + offset % 7)
                quantity = self.seed['days'][source]['usage'][indices[value['channel']]]
                value['prediction'] = None if quantity is None else str(quantity)
                value['prediction_raw'] = value['prediction']
            values.append(value)
        return values

    def history(self, state: dict, item_id: str, origin: str, model: str = 'timesfm') -> dict:
        index = next((i for i, item in enumerate(self.items) if item['id'] == item_id), None)
        if index is None:
            raise KeyError('ITEM_NOT_FOUND')
        item = self.items[index]
        prior = max((o for o in state['origins'] if o < origin), default=None)
        def curve(o: str | None) -> dict | None:
            return None if o is None else {'id': self.forecast(state, o)['id'], 'origin': o, 'values': [{'day': v['target_day'], 'quantity': v['prediction']} for v in self.prediction_values(o, model) if v['channel'] == item['code']]}
        previous = curve(prior)
        actual = [{'day': shifted(origin, n), 'quantity': None if (v := self.seed['days'][shifted(origin, n)]['usage'][index]) is None else str(v), 'observation': 'missing' if v is None else 'observed'} for n in range(-28, 0)]
        truth = {v['day']: Decimal(v['quantity']) for v in actual if v['quantity'] is not None}
        eligible = [v for v in previous['values'] if v['day'] < origin] if previous else []
        # Keep both model metrics on the same observed weekday-repeat support.
        common_days = {v['target_day'] for v in self.prediction_values(prior, 'naive') if v['channel'] == item['code'] and v['prediction'] is not None} if prior else set()
        matched = [(Decimal(v['quantity']), truth[v['day']]) for v in eligible if v['day'] in truth and v['day'] in common_days and v['quantity'] is not None]
        absolute = sum((abs(p - a) for p, a in matched), Decimal(0))
        denominator = sum((abs(a) for _, a in matched), Decimal(0))
        rounded = lambda v: str(v.quantize(Decimal('0.000001'))) if v is not None else None
        return {'item_id': item_id, 'item_name': item['name'], 'unit': item['base_unit'], 'as_of': stamp(origin), 'current': curve(origin), 'previous': previous, 'actual': actual, 'metrics': {'observations': len(matched), 'expected_observations': len(eligible), 'mae': rounded(absolute / len(matched)) if matched else None, 'wape_pct': rounded(absolute / denominator * 100) if denominator else None, 'start': min((v['day'] for v in eligible), default=None), 'end': max((v['day'] for v in eligible), default=None)}}


def create_app(seed_path: Path, config_path: Path, database_path: Path, token: str, demo_mode: bool = True) -> FastAPI:
    """Create a replay-only app; refuse startup without a private access token."""
    if len(token) < 32:
        raise ValueError('PRIVATE_TOKEN_REQUIRED')
    store = DemoStore(seed_path, config_path, database_path)
    app = FastAPI(title='MTECH replay service', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.get('/api/v1/health/live')
    def health() -> dict:
        return {'status': 'ok'}

    @app.api_route('/api/v1/{path:path}', methods=['GET', 'POST'])
    async def route(path: str, request: Request) -> JSONResponse:
        if not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
            return JSONResponse({'error': {'code': 'UNAUTHORIZED'}}, status_code=401)
        try:
            model = request.query_params.get('model', 'timesfm')
            if model not in ('timesfm', 'naive'):
                raise ValueError('INVALID_MODEL')
            if (model != 'timesfm' or path == 'demo/settings') and not demo_mode:
                raise KeyError('DEMO_DISABLED')
            raw = await request.body() if request.method == 'POST' else b''
            if len(raw) > 16384:
                raise ValueError('PAYLOAD_TOO_LARGE')
            with store.connect() as conn:
                scope = request.headers.get('X-Workspace-ID', '')
                state = store.get(conn, scope) if scope else None
                def paged(values: list) -> dict:
                    limit = int(request.query_params.get('limit', '100'))
                    cursor = int(request.query_params.get('cursor', '0'))
                    if not 1 <= limit <= 100 or cursor < 0:
                        raise ValueError('INVALID_PAGE')
                    return {'items': values[cursor:cursor+limit], 'next_cursor': str(cursor + limit) if cursor + limit < len(values) else None}
                if request.method == 'GET':
                    if path == 'demo/capabilities':
                        result = {'demo_mode': demo_mode}
                    elif path == 'me':
                        result = {'id': uid('demo-admin'), 'platform_role': 'test_admin', 'workspace_id': scope or None, 'roles': ['admin', 'viewer', 'test_operator', 'operator'] if state else []}
                    elif path == 'demo/settings':
                        result = {**store.settings(conn), 'available_start_dates': store.origins, 'seed_sha256': store.seed['sha256'], 'mode': 'prerecorded_replay'}
                    elif path == 'test/scenarios':
                        result = paged([store.scenario(store.get(conn, r[0])) for r in conn.execute('SELECT id FROM scopes ORDER BY rowid').fetchall()])
                    elif state is None:
                        raise KeyError('WORKSPACE_REQUIRED')
                    elif path == 'test/scenarios/' + scope:
                        result = store.scenario(state)
                    elif path == 'items':
                        result = paged(store.items)
                    elif path in ('inventory', 'order-proposals', 'purchase-orders'):
                        result = paged([])
                    elif path == 'jobs':
                        result = paged(state['jobs'])
                    elif path.startswith('jobs/'):
                        result = next(j for j in state['jobs'] if j['id'] == path.split('/')[1])
                    elif path == 'forecasts':
                        result = paged([store.forecast(state, o) for o in state['origins']])
                    elif path == 'weekly-deliveries':
                        result = paged([store.delivery(state, o) for o in state['origins']])
                    elif path.startswith('forecasts/'):
                        parts = path.split('/')
                        origin = store.find_origin(state, parts[1])
                        if len(parts) != 3:
                            raise KeyError('NOT_FOUND')
                        if parts[2] == 'values':
                            result = paged([{'item_id': uid(v['channel']), 'target_day': v['target_day'], 'horizon_day': (date.fromisoformat(v['target_day']) - date.fromisoformat(origin)).days + 1, 'prediction': v['prediction'], 'prediction_raw': v['prediction_raw']} for v in store.prediction_values(origin, model)])
                        elif parts[2] == 'patient-history':
                            result = {'as_of': stamp(origin), 'forecast_origin': origin, 'values': [{'day': shifted(origin, n), **store.seed['days'][shifted(origin, n)]['patients']} for n in range(-28, 0)]}
                        else:
                            raise KeyError('NOT_FOUND')
                    elif path.startswith('items/') and path.endswith('/forecast-history'):
                        origin = store.find_origin(state, request.query_params.get('forecast_id', ''))
                        result = store.history(state, path.split('/')[1], origin, model)
                    else:
                        raise KeyError('NOT_FOUND')
                    return JSONResponse(result)
                body = json.loads(raw)
                canonical = json.dumps(body, sort_keys=True)
                key = request.headers.get('Idempotency-Key', '')
                if not key or len(key) > 200:
                    raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
                binding = scope + ':' + path
                old = conn.execute('SELECT body,response FROM requests WHERE path=? AND key=?', (binding, key)).fetchone()
                if old:
                    if old[0] != canonical:
                        raise RuntimeError('IDEMPOTENCY_KEY_CONFLICT')
                    return JSONResponse(json.loads(old[1]), status_code=202)
                if path == 'demo/settings':
                    config = Settings.model_validate(body).model_dump(mode='json')
                    store.validate_start(config['start_date'])
                    created = store.create_scope(conn, config['start_date'])
                    conn.execute("UPDATE metadata SET value=? WHERE key='settings'", (json.dumps(config),))
                    store.event(conn, 'settings.updated', config)
                    result = {'scenario_id': created['id'], **config}
                elif path == 'test/scenarios':
                    origin = body.get('first_week_start', '')
                    created = store.create_scope(conn, origin)
                    job_id = str(uuid4())
                    created['jobs'].append(make_job(job_id, 'bootstrap', 'succeeded'))
                    store.save(conn, created)
                    result = {'scenario_id': created['id'], 'job_id': job_id, 'status': 'queued'}
                elif state is None:
                    raise KeyError('WORKSPACE_REQUIRED')
                elif path == f'test/scenarios/{scope}/weekly-deliveries':
                    if body != {'expected_version': state['version'], 'week_start': state['origin']} or state['pending']:
                        raise RuntimeError('STALE_WEEK')
                    origin = shifted(state['origin'], 7)
                    if origin not in store.origins:
                        raise RuntimeError('PRERECORDED_FORECAST_UNAVAILABLE')
                    job_id = str(uuid4())
                    state['pending'] = {'origin': origin, 'ready_at': time.time() + store.settings(conn)['forecast_loading_seconds']}
                    state['version'] += 1
                    state['jobs'].append(make_job(job_id, 'forecast', 'running'))
                    store.save(conn, state)
                    store.event(conn, 'forecast.requested', {'scope_id': scope, **state['pending']})
                    result = {'resource_id': scope, 'job_id': job_id, 'status': 'queued'}
                elif path == f'test/scenarios/{scope}/reset-last-week':
                    if body != {'expected_version': state['version']} or state['pending']:
                        raise RuntimeError('STALE_WEEK')
                    origin = shifted(state['origin'], -7)
                    store.validate_start(origin)
                    created = store.create_scope(conn, origin)
                    job_id = str(uuid4())
                    created['jobs'].append(make_job(job_id, 'bootstrap', 'succeeded'))
                    store.save(conn, created)
                    result = {'scenario_id': created['id'], 'job_id': job_id, 'status': 'queued'}
                else:
                    raise KeyError('OPERATION_NOT_AVAILABLE')
                conn.execute('INSERT INTO requests VALUES (?,?,?,?)', (binding, key, canonical, json.dumps(result)))
                return JSONResponse(result, status_code=202)
        except (KeyError, StopIteration):
            return JSONResponse({'error': {'code': 'NOT_FOUND'}}, status_code=404)
        except RuntimeError as error:
            return JSONResponse({'error': {'code': str(error)}}, status_code=409)
        except (ValueError, TypeError):
            return JSONResponse({'error': {'code': 'INVALID_SETTINGS_OR_REQUEST'}}, status_code=422)

    return app


def make_job(identifier: str, stage: str, status: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {'id': identifier, 'parent_job_id': None, 'stage': stage, 'status': status, 'current_attempt': 1, 'heartbeat_at': now, 'retry_at': None, 'error_code': None, 'retryable': False, 'result': {'child_job_ids': [], 'order_ids': []}, 'updated_at': now}


def from_environment() -> FastAPI:
    """Load private mounts when Uvicorn starts the demo service."""
    return create_app(Path(os.getenv('DEMO_SEED', '/private/seed.json')), Path(os.getenv('DEMO_CONFIG', '/config/config.json')), Path(os.getenv('DEMO_DATABASE', '/state/demo.sqlite3')), Path(os.getenv('DEMO_TOKEN_FILE', '/private/access-token')).read_text().strip(), demo_mode=os.getenv('DEMO_MODE', 'false').lower() == 'true')
