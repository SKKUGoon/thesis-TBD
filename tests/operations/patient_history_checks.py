"""Verify aggregate dashboard data against registered source and scope isolation."""
from __future__ import annotations
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from system_scenario import Client

def run() -> None:
    root=Path(__file__).resolve().parents[2]
    c=Client('http://127.0.0.1:8090/api/v1',(root/'deploy/.state/test-token').read_text().strip())
    evidence=json.loads((root/'runs/cheonan/records_ui_20261022_01/records_checks.json').read_text());c.scope=evidence['scope_id']
    result=c.get('/forecasts/'+evidence['current_forecast_id']+'/patient-history')
    source=json.loads((root/'deploy/.state/records/cheonan-records-v1.json').read_text())
    assert len(result['values'])==28
    keys={'total':'total__ALL','inpatient':'encounter__IN','outpatient':'encounter__OUT'}
    for row in result['values']:
        assert row['day']<result['forecast_origin']
        for name,key in keys.items():assert row[name]==source['days'][row['day']]['patients'][source['patient_keys'].index(key)]
    import urllib.request,urllib.error
    other=json.loads((root/'runs/cheonan/web_videos_20261028_01/workspace.json').read_text())['scope_id']
    url=c.base+'/forecasts/'+evidence['current_forecast_id']+'/patient-history'
    for headers,expected in [({},401),({'Authorization':'Bearer '+c.token,'X-Workspace-ID':other},404)]:
        try:
            urllib.request.urlopen(urllib.request.Request(url,headers=headers));raise AssertionError('UNEXPECTED_ACCESS')
        except urllib.error.HTTPError as e:assert e.code==expected,(e.code,expected)
    output=root/'runs/cheonan/dashboard_ui_20261028_01/patient_checks.json'
    output.write_text(json.dumps({'status':'succeeded','days':28,'series':3,'checks':['all_values_match_registered_aggregate_source','no_future_dates','unauthenticated_401','foreign_workspace_forecast_404']},indent=2)+'\n')
    print('28 aggregate days match source; cutoff and authentication/scope checks passed.')

if __name__=='__main__':run()
