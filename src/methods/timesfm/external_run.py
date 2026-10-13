"""Forecast two separately frozen 20-drug cohorts and write report appendices."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import subprocess
import textwrap
import time

from huggingface_hub import snapshot_download
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from timesfm3.torch.timesfm3_forecaster import TimesFM3Forecaster

from src.methods.timesfm.adapter import MaskedTimesFM
from src.methods.timesfm.data import file_sha256
from src.methods.timesfm.run import build_origin_inputs, evaluate_predictions, forecast_origins
from src.methods.timesfm.seasonal_comparison import compare_saved_drug_forecasts
from src.methods.xgboost.known_calendar import read_known_calendar


def add_wape(metrics: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    """Attach per-product observed-label WAPE without pooling physical units."""
    observed = predictions[predictions.observed].copy()
    observed['abs_error'] = (observed.prediction-observed.actual).abs()
    totals = observed.groupby('channel').agg(abs_error=('abs_error','sum'),actual_total=('actual','sum'))
    totals['wape_percent'] = 100*totals.abs_error/totals.actual_total
    return metrics.merge(totals[['wape_percent']],left_on='channel',right_index=True,how='left')


def format_metric(value: float, suffix: str = '', decimals: int = 2) -> str:
    """Display unavailable metrics explicitly, without assigning a substitute."""
    return f'{value:.{decimals}f}{suffix}' if np.isfinite(value) else '평가 불가'


def write_external_appendix(output: Path, hospital: str, cohort: pd.DataFrame, forecasts: pd.DataFrame,
                           metrics: pd.DataFrame, comparison: pd.DataFrame, summary: dict[str, object],
                           dataset_manifest: dict[str, object]) -> None:
    """Write named-product chart and accessible accuracy-only appendix."""
    label = '이푸른병원' if hospital=='epurun' else '서송병원'
    number = 3 if hospital=='epurun' else 4
    plt.rcParams['font.family'] = 'AppleGothic'
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(20,1,figsize=(14,44),squeeze=False)
    for axis, info in zip(axes[:,0],cohort.itertuples()):
        d = forecasts[forecasts.channel.eq(info.channel)].sort_values('origin').drop_duplicates('target_date',keep='last').sort_values('target_date')
        axis.plot(d.target_date,d.actual,color='#666666',linewidth=.8,label='실제 처방 기록')
        axis.plot(d.target_date,d.prediction,color='#e66101',linewidth=.8,label='TimesFM')
        axis.set_title('\n'.join(textwrap.wrap(info.name,70)),fontsize=9)
        axis.set_ylabel('기록 수',fontsize=8);axis.legend(fontsize=8)
    fig.tight_layout()
    chart = f'{hospital}_timesfm_forecasts.png'
    fig.savefig(output/chart,dpi=110);plt.close(fig)
    selection = '2021–2022년' if hospital=='epurun' else '2022년'
    scope = ('환자·청구 CSV의 원문 약품 제품코드별 처방·청구 행 수' if hospital=='epurun'
             else '2022–2024년 외래 파일의 약품 보험코드별 처방 행 수')
    limitations = ('원본 q_per_c의 수량 단위·투약 일수 해석이 미확인이라 값을 합산하거나 물리적 소모량으로 환산하지 않았다. 입원/외래를 식별하는 확정 열이 없어 해당 환자 구분은 OTHER에 보존했다. 보험 유형을 입원/외래 구분으로 사용하지 않았다.' if hospital=='epurun' else
                   '입원·병동 파일은 뒤늦게 기록된 처방을 과거 입원일에 더할 위험이 있어 제외했다. 이번 결과는 서송 전체 환자나 입원 약품 수요의 결과가 아니다. 외래 처방에는 원외 처방도 포함될 수 있어 병원에서 직접 소모한 수량으로 해석하지 않는다. 주상병 표시 Y와 다른 행의 진단 구분은 원문 플래그를 따른 잠정 해석이다.')
    table = '\n'.join(f'| {r.name} | {format_metric(r.mae,decimals=3)} | {format_metric(r.wape_percent,"%")} | {format_metric(r.mase,decimals=3)} | {r.observations}/{r.possible_labels} |' for r in metrics.sort_values('channel').itertuples())
    paired = '\n'.join(f'| {r.name} | {format_metric(r.baseline_wape_percent,"%")} | {format_metric(r.timesfm_wape_percent,"%")} | {format_metric(r.relative_error_reduction_percent,"%")} | {r.common_rows} |' for r in comparison.itertuples())
    signed = float(summary['relative_baseline_error_reduction_percent'])
    outcome = f'예측 오차가 {signed:.2f}% 줄었다' if signed>=0 else f'예측 오차가 {-signed:.2f}% 늘었다'
    text = f'''## 부록 {number}. {label} 약품 20개 TimesFM 예측 정확도

### {number}.1. 실제 약품과 예측 목표

{label}의 {cohort.name.iloc[0]}, {cohort.name.iloc[1]}, {cohort.name.iloc[2]} 등을 포함한 약품 20개에 대해 앞으로 14일의 일별 **처방·청구 기록 수**를 예측했다. 목표는 {scope}다. 전체 이름과 결과는 아래 품목별 표에 제시한다.

기존 집계의 중복 제거·날짜 해석을 답습하지 않고 원본에서 다시 집계했다. 반복 약품 행을 모두 세었으며 무기록은 0으로 채우지 않았다. 이 결과는 약품의 물리적 소모 수량이나 구매 발주량을 직접 검증한 결과가 아니다. {limitations}

### {number}.2. 사용 변수와 시간 범위

| 항목 | 적용 내용 |
|---|---|
| 품목 선정 | {selection} 처방·청구 행 수 상위 20개 제품. 명단과 표시명은 2022년 말까지의 기록으로 고정 |
| 약품 구분 | 제공된 2025년 ATC 참조표에 연결되는 제품코드. ATC 군의 수량을 합치지 않고 제품별 목표 유지 |
| 과거 목표 | 선택된 약품 20개의 이전 {summary['selected_context']}일 기록 수, 결측 마스크 유지 |
| 과거 환자 | 이전 28일의 전체·성별·입원/외래 또는 분류불가·10세 연령·주/부상병 KCD 대분류 집계 69열 |
| 미래 날짜 | 원점 당시 알려진 요일·공휴일·전후일·주말·연간 주기·정부 거리두기 대응기간 |
| 개발 기간 | 2023-01-02–06-12의 월요일 24개 원점에서 목표 이력 28/56/112일 비교 |
| 평가 기간 | 2024-01-01–12-16의 월요일 51개 원점. 마지막 예측 대상일 2024-12-29 |

환자 집계는 원문 기록에 등장한 고유 환자 기준이며 기관 전체 내원 명부로 보지 않는다. 환자 기록이 없는 날짜는 결측으로 유지한다. 기록이 있는 날짜의 해당 범주 인원 0은 범주별 집계 결과이며 약품 수요의 결측 보완과 다르다. 2025년 참조표를 이용한 약품 식별은 사후 분류이며 당시 참조정보 가용성을 입증하지 않는다.

### {number}.3. 모델과 예측 절차

Google [TimesFM 3.0](https://github.com/google-research/timesfm)의 천안 실험과 같은 고정 가중치를 사용했다. 약품 20개를 하나의 다변량 Y 벡터로 입력하고 함께 예측했다. 두 병원의 자료는 섞지 않았으며 환자 미래 실제값을 입력하지 않았다.

1. 초기 기록으로 약품 20개와 열 순서를 고정했다.
2. 원점 전날까지의 목표와 환자 기록 및 당시 알려진 달력을 구성했다.
3. 2023년 개발기간의 품목별 MASE 평균이 가장 작은 목표 이력 길이 {summary['selected_context']}일을 선택했다. 환자 이력은 28일로 유지했다.
4. 동결한 설정으로 2024년 매주 월요일부터 14일을 예측했다. 가중치를 새로 학습하거나 월별 재적합하지 않았다.
5. 공식 모델의 관측 마스크를 사용하고 중앙 분위수 예측을 0 이상으로 제한했다. 평균 대입·보간·반복 처방 제거는 하지 않았다.
6. 실제 기록이 있는 평가행에서 정확도를 계산하고, 원점 이전 마지막 7일을 반복한 단순 예측과 같은 날짜에서 비교했다.

### {number}.4. 실제값과 예측 그림

![{label} 약품 20개 실제 처방 기록과 TimesFM]({chart})

그림은 같은 날짜를 예측한 여러 원점 중 그 날짜 전에 생성된 가장 최근 예측을 표시한다. 수치 표는 51개 원점의 14일 예측 전체를 사용한다. 무기록은 그래프의 빈 구간으로 남겼으며 0사용이라고 판단하지 않았다.

### {number}.5. 예측 수치와 해석

| 약품 20개 요약 | 결과 |
|---|---:|
| TimesFM 평균 사용 규모 대비 오차율 WAPE | {summary['macro_wape_percent']:.2f}% |
| WAPE 평균에 포함된 평가 가능 품목/선정 품목 | {summary['evaluable_items']}/20 |
| 평균 과거 변동 대비 오차 MASE | {summary['macro_mase']:.3f} |
| MASE 평균에 포함된 품목/선정 품목 | {summary['mase_evaluable_items']}/20 |
| 실제 기록이 있는 평가행/전체 | {summary['observed_rows']:,}/{summary['possible_rows']:,} |
| 단순 예측 평균 WAPE, 공통 범위 | {summary['baseline_macro_wape_percent']:.2f}% |
| TimesFM 평균 WAPE, 공통 범위 | {summary['timesfm_common_macro_wape_percent']:.2f}% |
| 상대 오차 감소율 | {signed:+.2f}% |
| 단순 예측보다 개선/악화/동일 품목 | {summary['better_items']}/{summary['worse_items']}/{summary['tied_items']} |
| 단순 예측 비교 가능 품목/선정 품목 | {summary['common_evaluable_items']}/20 |

**지난주 같은 요일의 수량을 그대로 반복한 예측과 비교하면, TimesFM의 {outcome}.** 품목마다 같은 비중을 준 평균 WAPE로 개선을 측정했다. 공통 비교 범위는 {summary['common_rows']:,}행으로, 단순 예측의 과거 입력이 없는 행은 비교에서 제외했다. 위의 전체 TimesFM 오차율과 공통 범위 오차율을 혼합하지 않는다. 개선율이 음수이면 단순 예측보다 악화된 결과다.

| 실제 약품 이름 | MAE, 기록 행 | WAPE | MASE | 관측/전체 평가행 |
|---|---:|---:|---:|---:|
{table}

| 실제 약품 이름 | 지난주 요일 반복 WAPE | TimesFM WAPE | 상대 오차 감소율 | 공통 평가행 |
|---|---:|---:|---:|---:|
{paired}

**기술 설명:** WAPE는 품목별 절대오차 합계/관측 실제 기록 수 합계를 계산한 뒤 동일 가중 평균했다. MASE 분모는 {selection}의 관측 가능한 7일 차이 절대평균이다. 0·미정의 분모는 미정의로 남긴다. 둘째 예측 주에도 원점 이전 마지막 주를 반복하며, 미래 첫째 주 실제값은 사용하지 않는다. 평가행에는 중첩 날짜가 있어 독립 표본의 유의성 검정으로 해석하지 않는다.

평균은 해당 지표를 계산할 수 있는 품목에 한정한다. 평가 불가 품목은 20개 명단에서 제거하거나 다른 약품으로 바꾸지 않았으며, 표에 관측 행 수와 평가 불가 상태를 표시했다. 무기록은 사용 중단이나 0수요가 확인된 것이 아니다. 약품별 관측 날짜가 달라 공통 비교도 기록이 있는 날짜에 대한 조건부 성능이다.

자료 가용시각은 사건일 다음 날로 가정했으며 실제 수집·정정 시각은 확인되지 않았다. 2026년 기초모델을 과거 자료에 적용한 후향적 평가로, 사전학습 자료의 병원 중복 여부는 미확인이다. 천안과 목표·환자 범위가 다르므로 병원별 오차율 차이를 모델 성능의 우열로 단정하지 않는다. 실제 발주와 재고·소모 연결 자료가 충분하지 않아 이 부록에서는 주문 정책 효용성을 평가하지 않았다.
'''
    (output/'report.md').write_text(text)
    (output/'reader_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))


def run_external_hospitals(root: Path, config_path: Path) -> None:
    """Run development selection and retrospective evaluation with pinned weights."""
    config = json.loads(config_path.read_text())
    if not torch.backends.mps.is_available():
        raise RuntimeError('MPS unavailable; no silent CPU substitution')
    snapshot = Path(snapshot_download(config['checkpoint'],revision=config['checkpoint_revision'],local_files_only=True))
    adapter = MaskedTimesFM(TimesFM3Forecaster.from_pretrained(str(snapshot),device=config['device']),config['device'])
    calendar = read_known_calendar(root/config['calendar'])
    for hospital in config['hospitals']:
        started = time.perf_counter()
        output = root/'runs'/hospital/f'timesfm_20_{config["run_date"]}_01'
        output.mkdir(parents=True,exist_ok=True)
        frozen_path = output/'frozen_settings.json'
        if frozen_path.exists() and json.loads(frozen_path.read_text())['config'] != config:
            raise ValueError('Existing run settings differ; use a new run directory')
        dataset = root/'data/processed'/hospital/'analysis'/config['dataset_id']
        targets = pd.read_parquet(dataset/'daily_targets.parquet')
        patients = pd.read_parquet(dataset/'daily_patient_composition.parquet')
        cohort = pd.read_parquet(dataset/'frozen_cohorts.parquet')
        data_manifest = json.loads((dataset/'manifest.json').read_text())
        try:
            origin = pd.Timestamp(config['development'][0])
            channels = cohort.channel.head(3).tolist()
            before = build_origin_inputs(targets,patients,channels,origin,28,calendar)
            altered_y, altered_p = targets.copy(), patients.copy()
            altered_y.loc[origin:] = 999999
            altered_p.loc[origin:] = -999999
            after = build_origin_inputs(altered_y,altered_p,channels,origin,28,calendar)
            assert all(np.array_equal(a,b,equal_nan=True) for a,b in zip(before,after))
            a = adapter.predict_masked(*before,config['patient_days'])
            b = adapter.predict_masked(*after,config['patient_days'])
            np.testing.assert_allclose(a,b,atol=1e-5)
            old_p = before[1].copy()
            wide = build_origin_inputs(targets,patients,channels,origin,56,calendar)
            changed_p = wide[1].copy();changed_p[:,:,:-28] = 99999
            np.testing.assert_allclose(adapter.predict_masked(*wide,28),adapter.predict_masked(wide[0],changed_p,wide[2],28),atol=1e-5)
            all_metrics = []
            scores = []
            for context in config['target_context_candidates']:
                dev_path = output/f'context_{context}_development.parquet'
                if dev_path.exists():
                    dev = pd.read_parquet(dev_path)
                else:
                    dev = forecast_origins(adapter,targets,patients,cohort,pd.date_range(*config['development'],freq='W-MON'),context,config['patient_days'],calendar,'joint')
                    dev.to_parquet(dev_path,index=False)
                metric = evaluate_predictions(dev,targets,cohort)
                all_metrics.append(metric)
                scores.append((float(metric.mase.mean()),context))
            selected = min(scores)[1]
            pd.concat(all_metrics).to_parquet(output/'development_metrics.parquet',index=False)
            (output/'frozen_settings.json').write_text(json.dumps({'config':config,'selected_context':selected,'development_scores':scores},indent=2))
            forecast_path = output/'drug_joint_evaluation.parquet'
            if forecast_path.exists():
                forecast = pd.read_parquet(forecast_path)
            else:
                forecast = forecast_origins(adapter,targets,patients,cohort,pd.date_range(*config['evaluation'],freq='W-MON'),selected,config['patient_days'],calendar,'joint')
                forecast.to_parquet(forecast_path,index=False)
            metrics = add_wape(evaluate_predictions(forecast,targets,cohort),forecast)
            metrics.to_parquet(output/'prediction_metrics.parquet',index=False)
            comparison_rows,comparison = compare_saved_drug_forecasts(forecast,targets,cohort)
            comparison_rows.to_parquet(output/'seasonal_baseline_comparison.parquet',index=False)
            comparison.to_parquet(output/'seasonal_baseline_metrics.parquet',index=False)
            baseline = float(comparison.baseline_wape_percent.mean())
            model = float(comparison.timesfm_wape_percent.mean())
            summary = {'hospital':hospital,'items':len(cohort),'selected_context':selected,
                       'evaluable_items':int(metrics.wape_percent.notna().sum()),
                       'mase_evaluable_items':int(metrics.mase.notna().sum()),
                       'common_evaluable_items':int(comparison.timesfm_wape_percent.notna().sum()),
                       'macro_wape_percent':float(metrics.wape_percent.mean()),'macro_mase':float(metrics.mase.mean()),
                       'observed_rows':int(metrics.observations.sum()),'possible_rows':int(metrics.possible_labels.sum()),
                       'baseline_macro_wape_percent':baseline,'timesfm_common_macro_wape_percent':model,
                       'relative_baseline_error_reduction_percent':100*(baseline-model)/baseline,
                       'better_items':int((comparison.timesfm_wape_percent<comparison.baseline_wape_percent).sum()),
                       'worse_items':int((comparison.timesfm_wape_percent>comparison.baseline_wape_percent).sum()),
                       'tied_items':int((comparison.timesfm_wape_percent==comparison.baseline_wape_percent).sum()),
                       'common_rows':int(comparison.common_rows.sum())}
            write_external_appendix(output,hospital,cohort,forecast,metrics,comparison,summary,data_manifest)
            code_paths = [Path(__file__),root/'src/methods/timesfm/external_data.py',root/'src/methods/timesfm/run.py',root/'src/methods/timesfm/adapter.py',root/'src/methods/timesfm/seasonal_comparison.py']
            manifest = {'status':'completed','config':config,'dataset':str(dataset.relative_to(root)),
                        'dataset_manifest_sha256':file_sha256(dataset/'manifest.json'),
                        'selected_context':selected,'development_scores':scores,'summary':summary,
                        'checkpoint_files':{p.name:file_sha256(p) for p in snapshot.iterdir() if p.is_file()},
                        'weight_license':'timesfm-non-commercial-license-v1.0',
                        'future_change_input_and_prediction_invariant':True,'old_patient_mask_invariant':True,
                        'model_retrained':False,'order_policy_evaluated':False,'device':config['device'],
                        'elapsed_seconds':time.perf_counter()-started,'max_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        'code_sha256':{str(p.relative_to(root)):file_sha256(p) for p in code_paths},
                        'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                        'git_status':subprocess.check_output(['git','status','--porcelain'],text=True).splitlines(),
                        'output_sha256':{p.name:file_sha256(p) for p in output.iterdir() if p.is_file() and p.name not in {'manifest.json','failure.json'}},
                        'failure':None,
                        'evaluation_unavailable_items':metrics.loc[metrics.observations.eq(0),['channel','name']].to_dict('records')}
            failure_path = output/'failure.json'
            if failure_path.exists():
                history = output/'failure_history';history.mkdir(exist_ok=True)
                failure_path.rename(history/f'comparison_failure_{time.time_ns()}.json')
                manifest['recovered_failure_history'] = [p.name for p in history.iterdir()]
            (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
        except Exception as error:
            (output/'failure.json').write_text(json.dumps({'type':type(error).__name__,'message':str(error)},ensure_ascii=False,indent=2))
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=Path('configs/timesfm_external.json'))
    args = parser.parse_args()
    run_external_hospitals(Path.cwd(),args.config.resolve())
