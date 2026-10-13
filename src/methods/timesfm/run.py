from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import subprocess
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch

from src.methods.order_policy.simulation import InventoryScenario,calculate_as_of_protection_forecast,simulate_order_policy,summarize_inventory_policy
from src.methods.timesfm.adapter import load_and_check_mps_model
from src.methods.timesfm.data import file_sha256
from src.methods.xgboost.known_calendar import calculate_known_calendar_features,read_known_calendar


def build_origin_inputs(targets: pd.DataFrame, patients: pd.DataFrame, channels: list[str], origin: pd.Timestamp, context: int, calendar: object) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    """Slice only before origin and build calendar using origin-time announcements."""
    past=pd.date_range(origin-pd.Timedelta(days=context),periods=context)
    dates=pd.date_range(past[0],periods=context+14)
    y=targets.reindex(past)[channels].to_numpy(dtype='float32').T[None]
    p=patients.reindex(past).to_numpy(dtype='float32').T[None]
    known=calculate_known_calendar_features(pd.DatetimeIndex([origin]*len(dates)),dates,calendar)
    known.update({f'weekday_{i}':(dates.dayofweek==i).astype('float32') for i in range(7)})
    known['year_sin']=np.sin(2*np.pi*dates.dayofyear/365.25).astype('float32')
    known['year_cos']=np.cos(2*np.pi*dates.dayofyear/365.25).astype('float32')
    z=np.stack(list(known.values()))[None].astype('float32')
    return y,p,z


def forecast_origins(adapter: object, targets: pd.DataFrame, patients: pd.DataFrame, cohort: pd.DataFrame, origins: pd.DatetimeIndex, context: int, patient_days: int, calendar: object, mode: str) -> pd.DataFrame:
    """Generate joint/independent masked forecasts and observed labels, with no fitting."""
    rows=[];channels=cohort.channel.tolist()
    for oi,origin in enumerate(origins):
        y,p,z=build_origin_inputs(targets,patients,channels,origin,context,calendar)
        if mode=='joint': quantiles=adapter.predict_masked(y,p,z,patient_days)[0]
        else:
            outputs=[]
            for start in range(0,len(channels),4):
                batch=y[0,start:start+4,None,:]
                outputs.append(adapter.predict_masked(batch,np.repeat(p,len(batch),axis=0),np.repeat(z,len(batch),axis=0),patient_days)[:,0])
            quantiles=np.concatenate(outputs)
        for k,channel in enumerate(channels):
            for h,date in enumerate(pd.date_range(origin,periods=14)):
                actual=targets.at[date,channel];raw=float(quantiles[k,h,4])
                row={'channel':channel,'origin':origin,'target_date':date,'horizon_day':h+1,'input_end':origin-pd.Timedelta(days=1),'actual':actual,'prediction_raw':raw,'prediction':max(0.,raw),'context':context,'mode':mode,'observed':bool(pd.notna(actual))}
                row.update({f'q{q+1}':float(quantiles[k,h,q]) for q in range(9)})
                rows.append(row)
        if oi%10==0: print(f'{cohort.cohort.iloc[0]} {mode} C{context} origin {oi+1}/{len(origins)}',flush=True)
    return pd.DataFrame(rows)


def evaluate_predictions(frame: pd.DataFrame, targets: pd.DataFrame, cohorts: pd.DataFrame) -> pd.DataFrame:
    """Compute observed-only errors and prior-period weekly MASE denominators."""
    rows=[]
    for (channel,mode),group in frame.groupby(['channel','mode']):
        observed=group[group.observed];past=targets.loc[:'2022-12-31',channel];denom=float((past-past.shift(7)).abs().mean());error=observed.prediction-observed.actual
        info=cohorts.set_index('channel').loc[channel]
        rows.append({'channel':channel,'name':info['name'],'cohort':info.cohort,'mode':group['mode'].iloc[0],'context':int(group.context.iloc[0]),'observations':len(observed),'possible_labels':len(group),'coverage':len(observed)/len(group),'mae':float(error.abs().mean()),'rmse':float(np.sqrt((error**2).mean())),'bias':float(error.mean()),'mase':float(error.abs().mean()/denom) if denom>0 else np.nan,'mase_denominator':denom,'interval80_coverage':float(((observed.actual>=observed.q1)&(observed.actual<=observed.q9)).mean()),'origins':group.origin.nunique()})
    return pd.DataFrame(rows)


def compare_and_write_policy(output: Path, targets: pd.DataFrame, cohorts: pd.DataFrame, validation: pd.DataFrame, evaluation: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Select safety on development only and save paired observed-segment ledgers."""
    results=[];selection=[];failures=[]
    for info in cohorts.itertuples():
        past=targets.loc[:'2022-12-31',info.channel];protection=past+past.shift(-1);valid=protection.dropna()
        if len(valid)<30:
            failures.append({'channel':info.channel,'reason':'fewer than 30 observed prior protection windows'});continue
        level=float(valid.mean());scale=float(valid.std());initial=int(np.ceil(valid.quantile(.95)))
        scenario=InventoryScenario(1,1,1,50,5,initial)
        inputs={}
        for phase,frame,end in [('validation',validation,config['policy_development_end']),('evaluation',evaluation,config['policy_evaluation_end'])]:
            subset=frame[(frame.channel==info.channel)&(frame['mode']=='joint')]
            dates=pd.date_range(subset.origin.min(),end)
            prediction=calculate_as_of_protection_forecast(subset,dates,2)
            demand=targets.loc[prediction.index,info.channel].dropna()
            inputs[phase]=(demand,prediction.reindex(demand.index))
        if any(len(v[0])==0 for v in inputs.values()): failures.append({'channel':info.channel,'reason':'no observed policy rows'});continue
        frozen={}
        for policy in ['without_forecast','timesfm_joint']:
            demand,pred=inputs['validation'];candidates=[]
            for b in config['safety_multipliers']:
                base=pd.Series(level,index=demand.index) if policy=='without_forecast' else pred
                ledger=simulate_order_policy(demand,np.ceil(base+b*scale),scenario)
                candidates.append({'channel':info.channel,'policy':policy,'b':b,**summarize_inventory_policy(ledger)})
            eligible=[x for x in candidates if x['fill_rate']>=.95]
            chosen=min(eligible,key=lambda x:(x['cost'],x['b'])) if eligible else min(candidates,key=lambda x:(-x['fill_rate'],x['cost']))
            frozen[policy]=chosen;selection.extend([{**x,'selected':x==chosen,'development_target_met':bool(eligible)} for x in candidates])
        paired=[]
        for policy,chosen in frozen.items():
            demand,pred=inputs['evaluation'];base=pd.Series(level,index=demand.index) if policy=='without_forecast' else pred
            ledger=simulate_order_policy(demand,np.ceil(base+chosen['b']*scale),scenario)
            ledger.to_parquet(output/f'{info.channel}_{policy}_ledger.parquet',index=False)
            paired.append({'channel':info.channel,'name':info.name,'cohort':info.cohort,'policy':policy,'safety_multiplier':chosen['b'],'initial_stock':initial,**summarize_inventory_policy(ledger)})
        baseline=paired[0]
        for row in paired:
            row['cost_reduction_percent']=100*(1-row['cost']/baseline['cost'])
            row['paired_service_target_met']=bool(row['fill_rate']>=.95 and baseline['fill_rate']>=.95)
            results.append(row)
    pd.DataFrame(selection).to_parquet(output/'policy_development_candidates.parquet',index=False)
    (output/'policy_failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2))
    frame=pd.DataFrame(results);frame.to_parquet(output/'policy_evaluation_metrics.parquet',index=False)
    return frame


def write_report(output: Path, cohorts: pd.DataFrame, predictions: pd.DataFrame, metrics: pd.DataFrame, policy: pd.DataFrame, manifest: dict) -> None:
    """Write one integrated named-item report, figures, metrics, formula and assumptions."""
    plt.rcParams['font.family']='AppleGothic';plt.rcParams['axes.unicode_minus']=False
    names='\n'.join(f'| {r.cohort} | {r.name} | {r.unit} | {r.selection_score:,.0f} |' for r in cohorts.itertuples())
    table='\n'.join(f'| {r.name} | {r.mode} | {r.mae:.3f} | {r.rmse:.3f} | {r.mase:.3f} | {r.observations}/{r.possible_labels} |' for r in metrics.itertuples())
    pt='\n'.join(f'| {r.name} | {r.policy} | {r.cost:,.1f} | {r.cost_reduction_percent:.2f}% | {r.fill_rate*100:.2f}% | {r.average_inventory:.2f} | {r.initial_stock} | {"충족" if r.paired_service_target_met else "미달"} |' for r in policy.itertuples())
    figures=[]
    for kind in ['drug','material']:
        selected=cohorts[cohorts.cohort==kind]
        fig,axes=plt.subplots(len(selected),1,figsize=(14,2.2*len(selected)),squeeze=False)
        for axis,info in zip(axes[:,0],selected.itertuples()):
            frame=predictions[(predictions.channel==info.channel)&(predictions['mode']=='joint')].sort_values('origin').drop_duplicates('target_date',keep='last').sort_values('target_date')
            if kind=='material': axis.bar(frame.target_date,frame.actual,color='gray',width=.8,label='기록 출고량')
            else: axis.plot(frame.target_date,frame.actual,color='gray',linewidth=.8,label='사용 기록량')
            axis.plot(frame.target_date,frame.prediction,color='#e66101',linewidth=.8,label='TimesFM 공동 예측')
            axis.set_title(info.name);axis.legend(fontsize=7);axis.set_ylabel(info.unit,fontsize=7)
        fig.tight_layout();fig.savefig(output/f'{kind}_forecasts.png',dpi=110);plt.close(fig)
        figures.append(f'![{kind} 실제값과 예측]({kind}_forecasts.png)')
    summary=metrics.groupby(['cohort','mode']).agg(macro_mase=('mase','mean'),items=('channel','nunique')).reset_index()
    summary.to_parquet(output/'cohort_summary.parquet',index=False)
    (output/'report.md').write_text(f'''# 천안의료원 고빈도 약품·물품 TimesFM 벡터 예측 및 주문 정책 효용성 평가

## 1. 실제 약품·물품과 예측 목표

약품 20개는 사용기록 1행을 기본 단위 1개로 센 제품별 수량, 물품 10개는 기록 출고수량을 예측했다. 반복 약품 행을 삭제하지 않았다. 무기록은 미관측이며 실제 물품 소비·구매 주문과 동일하지 않다. 제품 연결은 2020–2022년 단일 표준코드로 확인한 범위이며 임상적 대체 관계를 뜻하지 않는다. 약품 기본 단위와 입고 포장 환산은 미확인이다. 물품 순위는 같은 기간 양의 출고 기록 일수다.

| 구분 | 실제 이름 | 단위 | 과거 선정 점수 |
|---|---|---|---:|
{names}

## 2. 사용 변수와 시간 범위

원점 전날까지 제품별 수량과 환자 69개 집계 채널(전체·남녀·입원외래·10세 연령·주상병/부상병 KCD 대분류·공란/분류불가)을 사용했다. 환자는 사용기록에 등장한 고유 환자이며 전체 내원 명부가 아니다. 환자 문맥은 28일로 고정했다. 미래 대상일 요일·대한민국 공식 공휴일·전후일·주말·연간 주기·거리두기 대응 Boolean은 해당 원점까지 발표된 정보만 사용했다. 병원 구매·상품 전환 정책은 제외했다. 정보 가용 시점은 사건일 다음 날이라는 미확인 시나리오다.

## 3. 모델

Google TimesFM 3.0 고정 사전학습 가중치를 사용했다. 약품 20채널과 물품 10채널은 별도 공동 예측하고 같은 입력의 품목별 독립 예측을 비교했다. PyTorch {manifest['preflight']['torch_version']}의 Apple MPS로 실행했다. [공식 저장소](https://github.com/google-research/timesfm). 고수준 함수의 자동 보간을 우회하고 공식 `model.decode`에 품목별 관측 마스크를 전달했다. 배열 계산의 마스크 위치 숫자 자리표시는 관측 0이나 원자료 보완이 아니다. 저장 목표는 NaN 그대로다. 마스크 이전 환자 값 변경 검사와 원점 이후 관측 변경 검사를 통과했다.

## 4. 문맥 선택·추론·예측 절차

2020–2022년으로 명단과 품목별 주간 MASE 분모를 동결했다. 2023년 상반기 공통 원점에서 목표 문맥 28/56/112일의 품목별 MASE 평균으로 각 코호트 문맥을 선정했다: {manifest['selected_contexts']}. 환자 문맥은 목표 길이와 독립적으로 마지막 28일만 관측 마스크로 노출했다. 2023년 하반기 원점에서 주문 안전재고를 선정하고 2024년 평가에는 동결했다. 주별 월요일 H=14일이며 학습 가중치를 월별 재적합하지 않았다. 문맥이 갱신되는 zero-shot 추론이며 Expanding 재학습이 아니다. 공동/독립 비교는 같은 원점·X·관측 마스크를 사용했다.

## 5. 실제값·예측 그림과 관찰

그림은 중복 대상일 중 최신 과거 원점의 예측만 표시한다. 정확도 표는 주별 H=14 원점의 모든 관측 목표를 사용한다. 물품 실제값은 막대로, 무기록은 빈칸으로 표시했다.

{chr(10).join(figures)}

## 6. 예측 수치와 해석

| 품목 이름 | 조건 | MAE | RMSE | MASE | 관측/전체 평가행 |
|---|---|---:|---:|---:|---:|
{table}

MAE·RMSE 단위는 해당 품목의 기본 단위다. MASE는 2020–2022년 관측 가능한 7일 차이 절대평균으로 나누었다. 0/미정의 분모는 미정의로 남겼다. 다른 단위의 원시 오차를 합산하지 않았다. 분위수 0.1–0.9 및 경험적 80% 포함률은 별도 지표 파일에 저장했으며 95% 공동 예측구간이라고 부르지 않는다. 확대 코호트의 XGBoost/CatBoost/LightGBM 재실행과 환자 지연 후보 전체 탐색은 이번 첫 TimesFM 실행에 포함되지 않았다.

## 7. 예측 활용·미활용 주문 정책 효용성

예측 미활용은 2020–2022년 보호기간 수량 평균의 고정 목표재고, 활용은 원점 당시 TimesFM 공동 예측의 앞으로 2일 합계에 선정 안전재고를 더한 목표재고다. 재고 위치(보유+미도착 주문)를 빼고 부족한 만큼만 발주한다. 실제 현행 병원 주문을 재현한 것이 아니다.

| 공통 설정 | 값 |
|---|---|
| 검토·납기 | 매일 검토, 납기 1일 |
| 최초 재고 | 품목별 과거 관측 2일 수량의 95분위수 올림, 두 정책 동일 |
| 가정 비용 | 보유 1/기본단위·일, 신규 부족 50/기본단위, 주문 5/회 |
| 서비스 목표 | 수량 충족률 95% |
| 안전재고 | 2023년 하반기 b=0/.5/1/1.5/2/3/4 중 목표 충족 최소비용; 없으면 최고 충족률 진단 |
| 평가 | 2024-01-01–12-26, 각 품목 양쪽 공통 예측 가용·실제 기록 날짜 |
| 무기록 | 0 대입 금지; 연속 관측 구간 사이 양쪽 같은 최초 재고로 초기화 |
| 부족 | 신규 미충족 수량, 이월 없이 lost sales 가정 |
| 비용 단위 | 가상 정규화 비용, 원화 아님 |

비용 구조는 보유·결품·발주를 합친 재고 의사결정 평가이며, [Goltsos 등(2022), Inventory–forecasting: Mind the gap, EJOR 299(2),397–419](https://orca.cardiff.ac.uk/id/eprint/143417/3/1-s2.0-S0377221721006500-main.pdf) 및 [Kourentzes·Trapero·Barrow, Optimising forecasting models for inventory planning](https://kourentzes.com/forecasting/wp-content/uploads/2020/05/Kourentzes_2019_Optimising_forecasting_models_for_inventory_planning.pdf)의 예측–재고 연결을 설계 근거로 삼았다. 논문 실험을 그대로 재현한 것은 아니다.

$$C_{{\\pi}}=h\\sum_t I_{{\\pi,t}}^+ + p\\sum_t U_{{\\pi,t}} + K\\sum_t\\mathbf{{1}}(q_{{\\pi,t}}>0),\\qquad E=100(1-C_{{\\mathrm{{forecast}}}}/C_{{\\mathrm{{without}}}})$$

I는 당일 종료 재고, U는 신규 미충족 수량, q는 발주량, h/p/K는 표의 가정 단가다. E는 본 연구가 정의한 비용 감소율이며 양수는 감소, 음수는 악화다. 양쪽 95% 충족 여부와 함께 해석한다. 실제 재고가 없어 최초 재고와 재고 경로는 시뮬레이션 가정이다. 다른 단위의 비용을 원화 총절감으로 합산하지 않는다.

| 실제 이름 | 정책 | 가정 비용 | 비용 감소율 | 충족률 | 평균 재고 | 최초 재고 | 양쪽 95% |
|---|---|---:|---:|---:|---:|---:|---|
{pt}

미달 품목은 비용 감소만으로 효율 개선으로 판정하지 않는다. 물품 구간 초기화가 빈번하면 이 결과는 관측 구간의 조건부 비교이며 실제 연속 운영 성과가 아니다. 품목별 주문·입고·보유·부족 원장은 같은 디렉터리에 보존했다.

## 8. 저장·검증과 한계

2024년은 이미 EDA와 예비 모델 평가를 본 후향적 자료다. 2026년 기초모델의 사전학습 자료 종료일·천안 자료 중복 여부는 확인되지 않았으므로 전역 무누출을 보장하지 않는다. 과제 입력의 시간 누출 검사와 기초모델 중복 위험을 구분한다. 이벤트 지연·사용 단위 환산·추출 완전성의 기관 확인은 미완료다. 환자 행·식별값을 보고하거나 업로드하지 않았다.

예측·분위수·품목 지표·개발 문맥 비교·정책 후보·원장·자료 및 코드 해시·장치 자원·체크포인트 정보는 [manifest](manifest.json)에 연결했다. 실패를 기존 결과로 대체하지 않는다. 이번 실행은 첫 TimesFM 고정 문맥 실험이며 전체 계획 완료나 실제 물류 개선을 주장하지 않는다.
''')
    from src.methods.timesfm.reader_report import write_reader_report
    write_reader_report(Path.cwd(), output)


def main(config_path: Path | None = None) -> None:
    root=Path.cwd();config_path=config_path or root/'configs/timesfm_vector.json';config=json.loads(config_path.read_text());output=root/'runs/cheonan'/config['run_id'];output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter();dataset=root/config['dataset'];cohorts=pd.read_parquet(dataset/'frozen_cohorts.parquet');targets=pd.read_parquet(dataset/'daily_targets.parquet');patients=pd.read_parquet(root/config['patient_dataset']);calendar=read_known_calendar(root/config['calendar'])
    adapter,preflight=load_and_check_mps_model();(output/'preflight.json').write_text(json.dumps(preflight,indent=2));print('preflight',preflight,flush=True)
    # Future targets/patients cannot enter this origin builder.
    origin=pd.Timestamp('2023-01-02');channels=cohorts.channel.head(3).tolist();before=build_origin_inputs(targets,patients,channels,origin,28,calendar)
    altered_y=targets.copy();altered_p=patients.copy();altered_y.loc[origin:]=999999;altered_p.loc[origin:]=999999
    after=build_origin_inputs(altered_y,altered_p,channels,origin,28,calendar)
    assert all(np.array_equal(a,b,equal_nan=True) for a,b in zip(before,after))
    small=adapter.predict_masked(*before,28);small2=adapter.predict_masked(*after,28);assert np.allclose(small,small2)
    preflight['future_change_input_and_prediction_invariant']=True
    selected={};development=[]
    for kind in ['drug','material']:
        cohort=cohorts[cohorts.cohort==kind];scores=[]
        for context in config['target_context_candidates']:
            frame=forecast_origins(adapter,targets,patients,cohort,pd.date_range(*config['context_development'],freq='W-MON'),context,28,calendar,'joint')
            frame.to_parquet(output/f'{kind}_context_{context}_development.parquet',index=False)
            metrics=evaluate_predictions(frame,targets,cohorts);score=float(metrics.mase.mean());scores.append((score,context));development.extend(metrics.to_dict('records'))
        selected[kind]=min(scores)[1]
    pd.DataFrame(development).to_parquet(output/'context_development_metrics.parquet',index=False);(output/'contexts_frozen.json').write_text(json.dumps(selected))
    validation=[];evaluation=[]
    for kind in ['drug','material']:
        cohort=cohorts[cohorts.cohort==kind];context=selected[kind]
        frame=forecast_origins(adapter,targets,patients,cohort,pd.date_range(*config['policy_development'],freq='W-MON'),context,28,calendar,'joint');validation.append(frame)
        for mode in ['joint','independent']:
            frame=forecast_origins(adapter,targets,patients,cohort,pd.date_range(*config['evaluation'],freq='W-MON'),context,28,calendar,mode);evaluation.append(frame);frame.to_parquet(output/f'{kind}_{mode}_evaluation.parquet',index=False)
    validation=pd.concat(validation,ignore_index=True);evaluation=pd.concat(evaluation,ignore_index=True);validation.to_parquet(output/'policy_validation_forecasts.parquet',index=False)
    metrics=evaluate_predictions(evaluation,targets,cohorts);metrics.to_parquet(output/'prediction_metrics.parquet',index=False)
    policy=compare_and_write_policy(output,targets,cohorts,validation,evaluation,config)
    from huggingface_hub import scan_cache_dir
    cache=scan_cache_dir();checkpoint=[{'revision':r.commit_hash,'files':{f.file_name:file_sha256(f.file_path) for f in r.files}} for repo in cache.repos if repo.repo_id==config['checkpoint'] for r in repo.revisions]
    sources=[config_path,dataset/'manifest.json',dataset/'daily_targets.parquet',dataset/'frozen_cohorts.parquet',root/config['patient_dataset'],root/config['calendar']/'korean_public_holidays.parquet',root/config['calendar']/'distancing_policy_events.parquet']
    code=list((root/'src/methods/timesfm').glob('*.py'))+[root/'src/methods/order_policy/simulation.py',root/'src/methods/xgboost/known_calendar.py']
    manifest={'status':'completed_first_timesfm_experiment','plan_version':'v1.14','config':config,'selected_contexts':selected,'preflight':preflight,'checkpoint':checkpoint,'weight_license':'timesfm-non-commercial-license-v1.0','elapsed_seconds':time.perf_counter()-started,'max_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'mps_driver_bytes':torch.mps.driver_allocated_memory(),'input_sha256':{str(p.relative_to(root)):file_sha256(p) for p in sources},'code_sha256':{str(p.relative_to(root)):file_sha256(p) for p in code},'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'git_status':subprocess.check_output(['git','status','--porcelain'],text=True).splitlines(),'no_original_data_imputation':True,'unresolved':['actual information availability','stock/lead/cost hospital verification','pretraining overlap','new boosting comparison','full independent patient lag tuning','lead/cost sensitivity'],'patient_columns':patients.columns.tolist()}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2));write_report(output,cohorts,evaluation,metrics,policy,manifest)
    print(metrics.groupby(['cohort','mode']).mase.mean(),flush=True);print(policy[policy.policy=='timesfm_joint'][['name','cost_reduction_percent','fill_rate','paired_service_target_met']].to_string(index=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=Path('configs/timesfm_vector.json'))
    main(parser.parse_args().config)
