"""Exercise test reset, request replay, source preservation and actual TimesFM replay."""
from __future__ import annotations
import json
from pathlib import Path
import urllib.error
import urllib.request
from uuid import uuid4
from system_scenario import Client


def run() -> None:
    root=Path(__file__).resolve().parents[2]
    token=(root/'deploy/.state/test-token').read_text().strip()
    source=json.loads((root/'runs/cheonan/dashboard_videos_20261028_01/workspace.json').read_text())['scope_id']
    c=Client('http://127.0.0.1:8090/api/v1',token,source)
    state=c.get('/test/scenarios/'+source)
    before={path:c.all(path) for path in ['/forecasts','/weekly-deliveries','/purchase-orders']}
    path='/test/scenarios/'+source+'/reset-last-week';body={'expected_version':state['version']};key=str(uuid4())
    assert c.request(path,{'expected_version':state['version']+100})[0]==409
    accepted=c.post(path,body,key=key);assert c.post(path,body,key=key)==accepted
    assert c.request(path,{'expected_version':state['version']+1},key)[0]==409
    for headers,expected in [({},401),({'Authorization':'Bearer '+token,'X-Workspace-ID':str(uuid4())},404)]:
        try:
            urllib.request.urlopen(urllib.request.Request(c.base+path,data=json.dumps(body).encode(),headers={**headers,'Content-Type':'application/json','Idempotency-Key':str(uuid4())}));raise AssertionError('UNEXPECTED_ACCESS')
        except urllib.error.HTTPError as e:assert e.code==expected,(e.code,expected)
    fresh=Client(c.base,token,accepted['scenario_id']);reset=fresh.wait_ready()
    assert reset['next_week_start']==state['last_completed_week_start']
    assert fresh.all('/forecasts')==[] and fresh.all('/purchase-orders')==[]
    assert fresh.request('/test/scenarios/'+fresh.scope+'/reset-last-week',{'expected_version':reset['version']})[0]==409
    assert len(fresh.all('/items'))==30
    fresh.post('/test/scenarios/'+fresh.scope+'/weekly-deliveries',{'expected_version':reset['version'],'week_start':reset['next_week_start']})
    advancing=fresh.get('/test/scenarios/'+fresh.scope)
    status,blocked=fresh.request('/test/scenarios/'+fresh.scope+'/reset-last-week',{'expected_version':advancing['version']})
    assert status==409 and blocked['error']['code'] in ('SCENARIO_BUSY','SCENARIO_VERSION_CONFLICT')
    ready=fresh.wait_ready(240);forecasts=fresh.all('/forecasts');assert len(forecasts)==1 and forecasts[0]['stored_values']==420
    assert ready['last_completed_week_start']==reset['next_week_start']
    assert c.get('/test/scenarios/'+source)==state
    assert {path:c.all(path) for path in before}==before
    assert c.post(path,body,key=key)==accepted
    output=root/'runs/cheonan/reset_last_week_20261028_01/api_checks.json'
    output.write_text(json.dumps({'status':'succeeded','source_scope_id':source,'test_scope_id':fresh.scope,'reset_week_start':reset['next_week_start'],'checks':['stale_version_409','same_key_same_scope','same_key_different_body_409','unauthenticated_401','workspace_mismatch_404','no_completed_week_409','busy_reset_409','reset_before_latest_week_no_forecasts_or_orders','replayed_week_real_timesfm_420_values','source_state_forecasts_deliveries_orders_unchanged']},indent=2)+'\n')
    print('Reset and actual TimesFM replay passed; original execution preserved.')

if __name__=='__main__':run()
