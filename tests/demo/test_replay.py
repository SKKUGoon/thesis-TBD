"""Durable replay, input validation and temporal disclosure checks on synthetic data."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from src.demo.api import create_app

TOKEN = 'synthetic-test-token-' * 3


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    items = [{'channel': f'{kind}_{i:02d}', 'cohort': kind, 'name': f'Synthetic {kind} {i}', 'unit': '개'} for kind, count in [('drug', 20), ('material', 10)] for i in range(1, count + 1)]
    origins = ['2024-03-04', '2024-03-11', '2024-03-18']
    forecasts = {origin: [{'channel': item['channel'], 'target_day': str(date.fromisoformat(origin) + timedelta(days=n)), 'prediction': '2.5', 'prediction_raw': '2.5'} for item in items for n in range(14)] for origin in origins}
    days = {str(date(2024, 2, 1) + timedelta(days=n)): {'usage': [None if n % 3 == 0 else 2] * 30, 'patients': {'total': 10, 'inpatient': 4, 'outpatient': 6}} for n in range(60)}
    seed = tmp_path / 'seed.json'; seed.write_text(json.dumps({'version': 1, 'items': items, 'forecasts': forecasts, 'days': days}))
    config = tmp_path / 'config.json'; config.write_text(json.dumps({'start_date': '2024-03-11', 'forecast_loading_seconds': 10}))
    return seed, config, tmp_path / 'demo.sqlite3'


def client_for(files: tuple[Path, Path, Path]) -> TestClient:
    return TestClient(create_app(*files, TOKEN), headers={'Authorization': 'Bearer ' + TOKEN})


def choose(client: TestClient) -> dict:
    scenario = client.get('/api/v1/test/scenarios').json()['items'][-1]
    client.headers['X-Workspace-ID'] = scenario['id']
    return scenario


def post(client: TestClient, path: str, body: dict, key: str = 'test-request'):
    return client.post('/api/v1/' + path, json=body, headers={'Idempotency-Key': key})


def test_auth_and_scope(files):
    client = client_for(files)
    assert client.get('/api/v1/me', headers={'Authorization': 'Bearer invalid'}).status_code == 401
    assert client.get('/api/v1/items').status_code == 404
    choose(client)
    assert len(client.get('/api/v1/items').json()['items']) == 30
    assert client.get('/api/v1/demo/settings').json()['mode'] == 'prerecorded_replay'


def test_replay_deadline_restart_and_idempotency(files):
    client = client_for(files); state = choose(client)
    path = f"test/scenarios/{state['id']}/weekly-deliveries"
    body = {'expected_version': state['version'], 'week_start': state['next_week_start']}
    with patch('src.demo.api.time.time', return_value=100):
        first = post(client, path, body)
        assert first.status_code == 202
        assert post(client, path, body).json() == first.json()
        assert post(client, path, {**body, 'expected_version': 999}).status_code == 409
    restarted = client_for(files); restarted.headers['X-Workspace-ID'] = state['id']
    with patch('src.demo.api.time.time', return_value=109):
        result = restarted.get('/api/v1/test/scenarios/' + state['id']).json()
        assert result['status'] == 'advancing'
        assert result['next_week_start'] == '2024-03-11'
    with patch('src.demo.api.time.time', return_value=110):
        result = restarted.get('/api/v1/test/scenarios/' + state['id']).json()
        assert result['status'] == 'ready'
        assert result['next_week_start'] == '2024-03-18'
        assert restarted.get('/api/v1/jobs').json()['items'][-1]['status'] == 'succeeded'
        assert post(restarted, path, body, 'stale-request').status_code == 409
        assert post(restarted, path, {'expected_version': result['version'], 'week_start': result['next_week_start']}, 'exhausted').status_code == 409


@pytest.mark.parametrize('settings', [{'start_date': '2024-03-12', 'forecast_loading_seconds': 10}, {'start_date': '2024-03-11', 'forecast_loading_seconds': -1}, {'start_date': '2024-03-11', 'forecast_loading_seconds': 601}, {'start_date': '2024-03-11', 'forecast_loading_seconds': True}])
def test_invalid_settings_preserve_state(files, settings):
    client = client_for(files)
    before = client.get('/api/v1/test/scenarios').json()
    assert post(client, 'demo/settings', settings).status_code == 422
    assert client.get('/api/v1/test/scenarios').json() == before


def test_settings_create_new_scope_and_preserve_old(files):
    client = client_for(files); previous = choose(client)
    response = post(client, 'demo/settings', {'start_date': '2024-03-04', 'forecast_loading_seconds': 0})
    assert response.status_code == 202
    assert response.json()['scenario_id'] != previous['id']
    assert len(client.get('/api/v1/test/scenarios').json()['items']) == 2
    restarted = client_for(files)
    assert restarted.get('/api/v1/demo/settings').json()['forecast_loading_seconds'] == 0


def test_cutoff_missing_values_pagination_and_forecast_scope(files):
    client = client_for(files); state = choose(client)
    forecast = client.get('/api/v1/forecasts').json()['items'][-1]
    item = client.get('/api/v1/items').json()['items'][0]
    history = client.get(f"/api/v1/items/{item['id']}/forecast-history", params={'forecast_id': forecast['id']}).json()
    assert all(v['day'] < forecast['forecast_origin'] for v in history['actual'])
    assert any(v['quantity'] is None and v['observation'] == 'missing' for v in history['actual'])
    assert history['metrics']['observations'] < history['metrics']['expected_observations']
    assert history['metrics']['mae'] == '0.500000'
    assert history['metrics']['wape_pct'] == '25.000000'
    path = f"/api/v1/forecasts/{forecast['id']}/values"
    values = []; cursor = None
    while True:
        result = client.get(path, params={'limit': 100, **({'cursor': cursor} if cursor else {})}).json()
        values.extend(result['items']); cursor = result['next_cursor']
        if cursor is None: break
    assert len(values) == 420
    assert client.get('/api/v1/inventory').json()['items'] == []
    post(client, 'demo/settings', {'start_date': '2024-03-04', 'forecast_loading_seconds': 0})
    choose(client)
    assert client.get(path).status_code == 404


def test_seed_tamper_rejected_on_restart(files):
    client_for(files)
    seed = json.loads(files[0].read_text()); seed['items'][0]['name'] = 'Changed'
    files[0].write_text(json.dumps(seed))
    with pytest.raises(ValueError, match='SEED_CHANGED'):
        client_for(files)
