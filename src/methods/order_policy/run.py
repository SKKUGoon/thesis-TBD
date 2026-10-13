from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.methods.arima.data import calculate_file_sha256
from src.methods.boosting.combined_report import combine_existing_forecast_and_policy_reports
from src.methods.order_policy.simulation import InventoryScenario, calculate_as_of_protection_forecast, simulate_order_policy, summarize_inventory_policy


MODELS=('without_forecast','xgboost','catboost','lightgbm')


def read_common_policy_inputs(forecast_run: Path, item: str, phase: str, end: str, protection_days: int) -> tuple[pd.Series,dict[str,pd.Series]]:
    target=pd.read_parquet(forecast_run/'targets.parquet')[item]
    forecasts={}
    first_date=None
    for model in MODELS[1:]:
        predictions=pd.read_parquet(forecast_run/f'{item}_{model}_{phase}.parquet')
        assert (predictions.training_end<predictions.origin).all() and (predictions.input_end<predictions.origin).all()
        first_date=predictions.origin.min()
        available=target.loc[first_date:end].dropna()
        forecasts[model]=calculate_as_of_protection_forecast(predictions,available.index,protection_days)
    common_index=forecasts['xgboost'].index
    for forecast in forecasts.values():
        common_index=common_index.intersection(forecast.index)
    demand=target.reindex(common_index)
    assert demand.notna().all()
    return demand,{name:values.reindex(common_index) for name,values in forecasts.items()}


def run_and_write_order_policy_comparison(config_path: Path, output: Path) -> None:
    started=time.perf_counter()
    output.mkdir(parents=True,exist_ok=False)
    config=json.loads(config_path.read_text())
    (output/'config_snapshot.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
    forecast_root=Path(config['forecast_run'])
    targets=pd.read_parquet(forecast_root/'targets.parquet')
    names=json.loads((forecast_root/'manifest.json').read_text())['names']
    candidate_rows=[]
    selected_rows=[]
    result_rows=[]
    for item in ('drug','material'):
        for lead in config['lead_days']:
            protection=lead+config['review_days']
            history=targets[item].loc[:config['stock_reference_end']]
            protected_history=history.rolling(protection,min_periods=protection).sum().dropna()
            if len(protected_history)<100:
                raise ValueError('Insufficient complete historical protection windows.')
            level=float(protected_history.mean())
            scale=float(protected_history.std(ddof=1))
            initial=int(np.ceil(protected_history.quantile(config['initial_stock_quantile'])))
            development_demand,development_forecasts=read_common_policy_inputs(forecast_root,item,config['policy_development_phase'],config['development_end'],protection)
            evaluation_demand,evaluation_forecasts=read_common_policy_inputs(forecast_root,item,config['evaluation_phase'],config['evaluation_end'],protection)
            for penalty in config['shortage_cost_units']:
                scenario=InventoryScenario(lead,config['review_days'],config['holding_cost_unit_day'],penalty,config['order_fixed_cost'],initial)
                candidates={}
                for policy in MODELS:
                    candidates[policy]=[]
                    for multiplier in config['safety_std_multipliers']:
                        base=pd.Series(level,index=development_demand.index) if policy=='without_forecast' else development_forecasts[policy]
                        stock=np.ceil(base+multiplier*scale)
                        ledger=simulate_order_policy(development_demand,stock,scenario)
                        row={'item':item,'lead_days':lead,'shortage_penalty':penalty,'policy':policy,'safety_multiplier':multiplier,'reference_level':level,'reference_std':scale,'initial_stock':initial,**summarize_inventory_policy(ledger)}
                        candidate_rows.append(row)
                        candidates[policy].append(row)
                    eligible=[row for row in candidates[policy] if row['fill_rate']>=config['service_target']]
                    selected=min(eligible,key=lambda row:(row['cost'],row['safety_multiplier'])) if eligible else min(candidates[policy],key=lambda row:(-row['fill_rate'],row['cost']))
                    selected={**selected,'development_service_target_met':bool(eligible),'selection_rule':'minimum cost at target fill; if infeasible highest fill diagnostic only','selection_end':config['development_end']}
                    selected_rows.append(selected)
                (output/'policy_selection_frozen.json').write_text(json.dumps(selected_rows,ensure_ascii=False,indent=2))
                evaluation_results=[]
                for policy in MODELS:
                    selected=selected_rows[-4+MODELS.index(policy)]
                    base=pd.Series(level,index=evaluation_demand.index) if policy=='without_forecast' else evaluation_forecasts[policy]
                    stock=np.ceil(base+selected['safety_multiplier']*scale)
                    ledger=simulate_order_policy(evaluation_demand,stock,scenario)
                    ledger['policy']=policy
                    ledger.to_parquet(output/f'{item}_L{lead}_p{int(penalty)}_{policy}_ledger.parquet',index=False)
                    row={'item':item,'lead_days':lead,'shortage_penalty':penalty,'policy':policy,'safety_multiplier':selected['safety_multiplier'],'initial_stock':initial,'development_service_target_met':selected['development_service_target_met'],**summarize_inventory_policy(ledger)}
                    evaluation_results.append(row)
                baseline=evaluation_results[0]
                for row in evaluation_results:
                    row['cost_reduction_percent']=100*(1-row['cost']/baseline['cost'])
                    row['service_target_met']=row['fill_rate']>=config['service_target']
                    row['paired_service_target_met']=bool(row['service_target_met'] and baseline['fill_rate']>=config['service_target'])
                    row['inventory_reduction_percent']=100*(1-row['average_inventory']/baseline['average_inventory'])
                    result_rows.append(row)
    candidates=pd.DataFrame(candidate_rows);results=pd.DataFrame(result_rows)
    candidates.to_parquet(output/'development_policy_candidates.parquet',index=False)
    results.to_parquet(output/'evaluation_policy_metrics.parquet',index=False)
    paths=[config_path,forecast_root/'targets.parquet',forecast_root/'manifest.json']+list(forecast_root.glob('*_validation.parquet'))+list(forecast_root.glob('*_evaluation.parquet'))
    manifest={'status':'completed','names':names,'elapsed_seconds':time.perf_counter()-started,'input_sha256':{str(path):calculate_file_sha256(path) for path in paths},'code_sha256':{str(path):calculate_file_sha256(path) for path in Path('src/methods/order_policy').glob('*.py')},'config':config,'cost_units':'hypothetical normalized cost, not KRW','actual_hospital_policy_reconstructed':False,'missing_demand_imputed':False,'development_demand_used_until':config['development_end'],'evaluation_not_used_for_selection':True}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    write_and_attach_order_policy_report(output,forecast_root,config,results,names)
    print(results[(results.lead_days==config['primary_lead_days'])&(results.shortage_penalty==config['primary_shortage_cost'])][['item','policy','cost','cost_reduction_percent','fill_rate','average_inventory','shortage_units','orders','days','segments','paired_service_target_met']].to_string(index=False))


def write_and_attach_order_policy_report(output: Path, forecast_root: Path, config: dict, results: pd.DataFrame, names: dict[str,str]) -> None:
    plt.switch_backend('Agg')
    primary=results[(results.lead_days==config['primary_lead_days'])&(results.shortage_penalty==config['primary_shortage_cost'])]
    table='\n'.join(f"| {names[row.item]} | {'예측 미활용' if row.policy=='without_forecast' else row.policy} | {row.cost:,.1f} | {row.cost_reduction_percent:.2f}% | {100*row.fill_rate:.2f}% | {row.average_inventory:.2f} | {row.shortage_units:.1f} | {row.shortage_days} | {row.orders} | {'충족' if row.service_target_met else '미달'} |" for row in primary.itertuples())
    scenario_table='\n'.join(f"| {names[row.item]} | {row.lead_days}일 | {row.shortage_penalty:g} | {row.policy} | {row.cost_reduction_percent:.2f}% | {100*row.fill_rate:.2f}% | {'양쪽 충족' if row.paired_service_target_met else '비용차만 해석 불가'} |" for row in results[results.policy!='without_forecast'].itertuples())
    comments=[]
    for item in ('drug','material'):
        subset=primary[primary.item==item]
        baseline=subset[subset.policy=='without_forecast'].iloc[0]
        eligible=subset[(subset.policy!='without_forecast')&subset.paired_service_target_met]
        if eligible.empty:
            conclusion='공통 95% 서비스 조건을 충족하는 예측 정책 비교가 없어 효율 개선으로 판정하지 않았다.'
        else:
            parts=[f"{row.policy}: 비용 {'감소' if row.cost_reduction_percent>0 else '증가'} {abs(row.cost_reduction_percent):.2f}%, 충족률 {row.fill_rate*100:.2f}%" for row in eligible.itertuples()]
            conclusion='; '.join(parts)+'.'
        comments.append(f"- **{names[item]}**: 예측 미활용 비용 {baseline.cost:,.1f}, 충족률 {baseline.fill_rate*100:.2f}%. {conclusion}")
    figure,axes=plt.subplots(2,2,figsize=(12,7))
    for column,item in enumerate(('drug','material')):
        subset=primary[primary.item==item]
        labels=['No forecast','XGBoost','CatBoost','LightGBM']
        axes[0,column].bar(labels,subset.cost,color=['gray','#d95f02','#7570b3','#1b9e77'])
        axes[0,column].set_title('Drug: continuous inventory' if item=='drug' else 'Forcep jar: observed segments')
        axes[0,column].set_ylabel('Hypothetical total cost')
        axes[1,column].bar(labels,subset.fill_rate*100,color=['gray','#d95f02','#7570b3','#1b9e77'])
        axes[1,column].axhline(config['service_target']*100,color='red',linestyle='--',label='95% target')
        axes[1,column].set_ylim(0,101);axes[1,column].set_ylabel('Quantity fill rate (%)');axes[1,column].legend()
    figure.tight_layout();figure.savefig(output/'policy_cost_service.png',dpi=150);plt.close(figure)
    figure,axes=plt.subplots(2,1,figsize=(14,6))
    for axis,item in zip(axes,('drug','material')):
        for policy,color in zip(MODELS,['gray','#d95f02','#7570b3','#1b9e77']):
            ledger=pd.read_parquet(output/f"{item}_L{config['primary_lead_days']}_p{int(config['primary_shortage_cost'])}_{policy}_ledger.parquet")
            inventory=ledger.set_index('date').end_inventory.reindex(pd.date_range(ledger.date.min(),ledger.date.max()))
            axis.plot(inventory.index,inventory,color=color,linewidth=.8,label=policy)
        axis.set_title('Drug: simulated end-day stock' if item=='drug' else 'Forcep jar: stock reset at unobserved gaps')
        axis.set_ylabel('Simulated inventory units');axis.legend(ncol=4)
    figure.tight_layout();figure.savefig(output/'inventory_paths.png',dpi=150);plt.close(figure)
    coverage='\n'.join(f"| {names[row.item]} | {row.days} | {row.segments} | {row.initial_stock} |" for row in primary[primary.policy=='without_forecast'].itertuples())
    report=f'''# 예측 활용·미활용 주문 정책의 물류 효율 비교

## 대상과 주문 정책

대상은 **{names['drug']}**의 소모기록과 **{names['material']}**의 기록 출고수량이다. 현재 병원 주문이 효율적인지 판정하는 것이 아니라 예측을 주문 결정에 추가했을 때의 증분 효율을 비교한다.

- **예측 미활용**: 2020–2022년 관측된 보호기간 수량으로 설정한 고정 목표재고를 유지하고 재고 위치만 보고 주문한다. 평가기간에 미래 수요 예측 또는 최신 수요 수준을 갱신하지 않는다. 병원의 실제 현행 정책을 재현한 것은 아니다.
- **예측 활용**: XGBoost·CatBoost·LightGBM의 원점 당시 예측으로 보호기간 목표재고를 갱신하며 같은 재고 위치·납기·검토주기 조건으로 주문한다. 예측 변수·모형·Expanding 적합은 [기존 품목 보고서](../boosting_two_items_20261007_01/report.md)의 실행을 사용했고 새로 적합하지 않았다.

## 설정 동결과 공통 환경

주 조건은 **매일 검토, 납기 1일, 보유비용 1/단위·일, 새로 미충족된 수량의 비용 50/단위, 주문 고정비 5/회, 수량 충족률 목표 95%**다. 원화·실제 병원 납기·단가가 아닌 연구 가정이다. 납기 3일과 부족 비용 10/50/100도 같은 절차로 민감도 평가했다. 수량은 캡슐/ea의 기본 단위이고 포장 환산은 미확인이다.

2020–2022년의 연속 완전 관측 보호기간 합계로 평균 $\\mu$와 표준편차 $\\sigma$를 설정했다. 이 통계는 정책 파라미터이며 원자료 결측에 대입하지 않았다. 시작 재고는 그 합계의 95% 경험분위수를 올림한 값으로 **같은 대상·납기의 모든 정책에서 동일**하다. 각 정책의 안전재고 계수 $b$는 같은 후보 {config['safety_std_multipliers']} 중 2023년 하반기(12월21일까지)의 95% 충족률을 만족하는 최소비용 후보로 선택했다. 미달이면 최대 충족률 후보를 진단용으로 기록하고 개선으로 판정하지 않는다. 2024년 성적으로 $b$·비용·납기를 재선택하지 않았다. 기존 예측 모델의 검증 구간을 이번 정책 설정의 개발 구간으로 사용한 후향적 연구다.


### 공통 설정 요약표

| 항목 | 주 분석 설정 | 동결·적용 기준 |
|---|---|---|
| 비교 정책 | 고정 목표재고의 예측 미활용 정책 / 예측에 따라 목표재고를 갱신하는 정책 | 동일 대상·수요 경로에서 비교 |
| 주문 검토주기 | {config['review_days']}일, 매일 검토 | 모든 정책에 동일 적용 |
| 납기 | 주 조건 {config['primary_lead_days']}일; 민감도 {config['lead_days']}일 | 동일 시나리오의 모든 정책에 동일 적용 |
| 보호기간 | 검토주기 + 납기; 주 조건 2일 | 해당 기간 수요를 기준으로 목표재고 설정 |
| 보유비용 | {config['holding_cost_unit_day']:g}/재고 1단위·일 | 실제 원화가 아닌 가정 비용 |
| 부족비용 | 주 조건 {config['primary_shortage_cost']:g}/미충족 1단위; 민감도 {config['shortage_cost_units']} | 신규 부족에 한 번만 부과 |
| 주문 고정비 | {config['order_fixed_cost']:g}/주문 1회 | 주문량이 양수일 때만 부과 |
| 서비스 목표 | 수량 충족률 {config['service_target']*100:g}% | 양쪽 정책 모두 목표를 충족해야 비용 감소를 효율 개선으로 해석 |
| 기준 수량 통계 | 2020–2022년 완전 관측 보호기간 합계의 평균·표준편차 | 정책 설정에 사용; 원자료 결측 대입 없음 |
| 시작 재고 | 같은 과거 합계의 {config['initial_stock_quantile']*100:g}% 경험분위수를 올림 | 같은 대상·납기의 모든 정책에서 동일 |
| 안전재고 계수 후보 | {config['safety_std_multipliers']} | 모든 정책에 같은 후보 제공 |
| 정책 설정 선택 기간 | 2023년 하반기–{config['development_end']} | 목표 충족 후보 중 최소비용; 없으면 진단용 최대 충족률 후보 |
| 평가 기간 | 2024-01-01–{config['evaluation_end']} | 평가 결과로 설정을 재선택하지 않음 |
| 부족 처리 | 미충족 수요 이월 없음 | 같은 부족수량의 반복 비용 부과 없음 |
| 수량 단위 | 약품 캡슐 / 물품 ea, 기본 단위 정수 올림 | 포장 환산은 미확인 |
| 약품 재고 경로 | 완전 관측 기간의 재고·미도착 주문을 연속 계산 | 정책 간 동일 시작 재고 |
| 물품 무기록 처리 | 연속 기록 구간만 평가, 공백 뒤 공통 시작 재고로 초기화 | 무기록을 0 또는 평균으로 채우지 않음 |
| 실제 병원 업무 재현 | 수행하지 않음 | 연구 가정 시나리오의 정책 비교 |

## 주문·재고·비용 수식

$$S_{{\\mathrm{{without}}}}=\\lceil\\mu+b\\sigma\\rceil,\\qquad S_{{\\mathrm{{forecast}},t}}=\\left\\lceil\\sum_{{j=0}}^{{R+L-1}}\\widehat D_{{t+j\\mid o(t)}}+b\\sigma\\right\\rceil$$

$R$은 검토주기 1일, $L$은 납기, $o(t)$는 당일 또는 이전의 가장 최근 예측원점이다. 미래 원점의 예측은 가져오지 않는다. 예측은 원점 전날까지의 환자/약품 입력으로 이미 생성된 값이다.

$$IP_t=I_t+P_t,\\qquad q_t=\\lceil\\max(0,S_t-IP_t)\\rceil$$

당일 도착분을 먼저 입고하고, 가용 재고 $I_t$와 미도착 주문 $P_t$를 합한 재고 위치로 주문한 다음 수요를 충족한다. 납기 $L$일 뒤 주문이 도착한다. 미충족은 이월하지 않는 lost-demand 가정이며 새로운 부족수량에 한 번만 비용을 부과한다.

$$V_t=\\min(I_t,D_t),\\quad U_t=D_t-V_t,\\quad I_{{t,\\mathrm{{end}}}}=I_t-V_t$$

$$C=\\sum_t\\left(hI_{{t,\\mathrm{{end}}}}+pU_t+K\\mathbf{{1}}(q_t>0)\\right),\\qquad F=\\frac{{\\sum_t V_t}}{{\\sum_t D_t}}$$

$$E=100\\left(1-\\frac{{C_{{\\mathrm{{forecast}}}}}}{{C_{{\\mathrm{{without}}}}}}\\right)$$

$h$는 보유, $p$는 부족, $K$는 주문 비용이다. $F$는 수량 충족률, $E$는 예측 미활용 대비 비용 감소율이다. 같은 목표 95%를 양쪽 모두 충족해야 비용 감소를 조건부 효율 개선으로 해석한다. 서비스 수준이 정확히 같다는 의미는 아니며 재고·부족·주문횟수도 함께 보고한다. 최종 재고와 미도착 주문을 임의로 청산하거나 잔존가치를 부여하지 않고 기록한다.

## 관측 범위와 물품 결측 처리

| 대상 | 평가 관측일 | 연속 평가 구간 수 | 공통 구간 시작 재고 |
|---|---:|---:|---:|
{coverage}

약품은 2024년 1월1일–12월26일의 연속 수요 경로에서 재고·주문을 이어 계산했다. 물품은 실제 출고기록이 연속된 구간에서만 계산하고 무기록 공백을 만나면 다음 구간을 같은 공통 시작 재고로 새로 시작했다. 공백 중 주문/재고를 복원하거나 수요를 0으로 대입하지 않았다. 따라서 **Forcep jar는 연속 기록 구간에 조건부인 정책 비교**이며 병원의 1년 전체 재고 효과가 아니다. 반복 초기화·구간 끝 미도착 주문 때문에 실제 장기 효과로 일반화할 수 없다. 모든 정책에서 동일 관측일·구간 경계·시작 재고를 사용했다.

## 2024년 주 조건 결과

![예측 활용·미활용 정책의 비용과 충족률](policy_cost_service.png)

| 대상 | 정책 | 가정 총비용 | 미활용 대비 비용 감소 | 충족률 | 평균 재고 | 부족수량 | 부족일 | 주문횟수 | 95% 목표 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
{table}

{chr(10).join(comments)}

![시뮬레이션 재고 경로](inventory_paths.png)

이 결과는 동등한 가정 환경에서 예측 활용 여부를 바꾼 정책 비교다. 현재 주문의 실제 효율성·원화 절감·임상 안전성의 실증 결과는 아니다. 모델별 안전재고를 개발에서 선택했으므로 단순한 점예측 추가만의 효과가 아니라 **예측과 사전 선택된 정책 설정의 결합 효과**다.

## 납기·비용 민감도

| 대상 | 납기 | 부족 단위 비용 | 예측 정책 | 비용 감소 | 충족률 | 서비스 비교 조건 |
|---|---:|---:|---|---:|---:|---|
{scenario_table}

## 선행논문·저장·검증

Kourentzes·Trapero·Barrow의 *Optimising forecasting models for inventory planning* ([저자 공개 원문](https://kourentzes.com/forecasting/wp-content/uploads/2020/05/Kourentzes_2019_Optimising_forecasting_models_for_inventory_planning.pdf))과 Goltsos 등 (2022)의 *Inventory – forecasting: Mind the gap* ([저자기관 원문](https://orca.cardiff.ac.uk/id/eprint/143417/3/1-s2.0-S0377221721006500-main.pdf))을 재고 정책·서비스 수준을 함께 평가하는 근거로 사용했다. 이 논문들의 실제 데이터·모델·결과를 복제했다고 주장하지 않는다. 위 비용은 명시한 보유·새 부족·주문 비용 구조이고 $E$는 본 연구의 정규화 정의다.

[설정](config_snapshot.json), [2023년 후보별 성적](development_policy_candidates.parquet), [2024년 평가 전 정책 선정](policy_selection_frozen.json), [정책별 결과](evaluation_policy_metrics.parquet), [입력·코드 해시](manifest.json), [정책 구현](../../../src/methods/order_policy/simulation.py), [실행·보고 구현](../../../src/methods/order_policy/run.py), [집중 검사](../../../tests/test_order_policy.py).

모든 정책의 날짜별 주문·도착·재고·수요·충족·부족·비용은 ledger.parquet에 저장했다. 재고 수량 보존, 주문=도착+미도착, 미래 예측원점 배제, 무기록 구간 초기화와 미충족 비용 중복 금지를 검사했다. 원자료와 기존 모델/예측은 변경하지 않았다.
'''
    (output/'report.md').write_text(report)
    combine_existing_forecast_and_policy_reports(forecast_root,output)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run_and_write_order_policy_comparison(args.config,args.output)
