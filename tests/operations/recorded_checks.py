"""Verify real aggregate ingestion, Linux forecasts and cutoff-safe comparison metrics."""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

from system_scenario import Client


def run(base: str, token: str, output: Path) -> None:
    evidence: dict = {'status':'running','checks':[],'source':'recorded_aggregates'}
    client=Client(base,token)
    try:
        created=client.post('/test/scenarios',{'name':'천안의료원 사용 기록 · 예측 비교','seed':20261022,'hospital_code':'CHEONAN','warehouse_code':'RECORDS','first_week_start':'2024-03-04','initial_history_days':112,'profile':'cheonan_records_v1'})
        client.scope=created['scenario_id'];evidence['scope_id']=client.scope
        state=client.wait_ready()
        assert state['data_source']=='records' and state['inventory_source']=='not_registered'
        items=client.all('/items');assert len(items)==30 and not any('가상' in item['name'] or '테스트' in item['name'] for item in items)
        assert client.all('/inventory')==[]
        evidence['checks'].append('30_actual_names_units_and_no_invented_inventory')
        for _ in range(2):
            client.post('/test/scenarios/'+client.scope+'/weekly-deliveries',{'expected_version':state['version'],'week_start':state['next_week_start']})
            state=client.wait_ready(240)
        forecasts=sorted(client.all('/forecasts'),key=lambda f:f['forecast_origin'])
        assert len(forecasts)==2 and all(f['stored_values']==420 for f in forecasts)
        assert client.all('/order-proposals')==[] and client.all('/purchase-orders')==[]
        item=next(i for i in items if i['code']=='drug_01')
        history=client.get('/items/'+item['id']+'/forecast-history?forecast_id='+forecasts[-1]['id'])
        assert history['item_name']=='타세놀이알650mg서방정 8시간'
        assert history['previous']['origin']<history['current']['origin']
        assert max(p['day'] for p in history['actual'])<history['current']['origin']
        actual={p['day']:Decimal(p['quantity']) for p in history['actual'] if p['quantity'] is not None and p['observation']=='observed'}
        pairs=[(Decimal(v['quantity']),actual[v['day']]) for v in history['previous']['values'] if v['day'] in actual]
        errors=sum((abs(p-y) for p,y in pairs),Decimal(0))
        assert len(pairs)==history['metrics']['observations']==7
        assert abs(Decimal(history['metrics']['mae'])-errors/len(pairs))<=Decimal('0.000001')
        assert abs(Decimal(history['metrics']['wape_pct'])-errors/sum((y for _,y in pairs),Decimal(0))*100)<=Decimal('0.000001')
        evidence.update(current_forecast_id=forecasts[-1]['id'],previous_forecast_id=forecasts[0]['id'],item_id=item['id'],item_name=item['name'],metrics=history['metrics'])
        evidence['checks'].extend(['two_actual_linux_forecasts_840_values','prior_actual_current_curves_with_future_truth_excluded','independent_mae_wape_match_on_7_observed_days','no_orders_without_real_stock_or_purchasing_policy'])
        evidence['status']='succeeded'
    except Exception as error:
        evidence.update(status='failed',error=str(error));raise
    finally:
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:8090/api/v1');parser.add_argument('--token-file',type=Path,default=Path('deploy/.state/test-token'));parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    run(a.base,a.token_file.read_text().strip(),a.output)
