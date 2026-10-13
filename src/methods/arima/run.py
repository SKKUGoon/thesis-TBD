from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import resource
import subprocess
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .data import build_and_write_group_dataset, calculate_file_sha256
from .forecasting import calculate_fixed_seasonal_scale, calculate_group_forecast_metrics, run_monthly_expanding_arima


plt.switch_backend('Agg')

def build_completed_monday_origins(start: str, end: str, horizon_days: int) -> pd.DatetimeIndex:
    return pd.date_range(start, pd.Timestamp(end) - pd.Timedelta(days=horizon_days - 1), freq='W-MON')


def build_and_score_past_allocation(predictions: pd.DataFrame, daily_items: pd.DataFrame, item_codes: list[str], window_days: int, delay_days: int) -> tuple[pd.DataFrame, float | None]:
    rows = []
    for origin, frame in predictions.groupby('origin'):
        last_available_date = origin - pd.Timedelta(days=delay_days)
        history = daily_items[(daily_items['date'] <= last_available_date) & (daily_items['date'] > last_available_date - pd.Timedelta(days=window_days))]
        recorded_totals = history.groupby('item_code')['recorded_count'].sum().reindex(item_codes)
        denominator = recorded_totals.sum()
        if len(history['date'].unique()) != window_days or denominator <= 0:
            continue
        # Ratios use recorded export counts only; they are not confirmed clinical selection probabilities.
        shares = recorded_totals / denominator
        for record in frame.to_dict('records'):
            actual_items = daily_items[daily_items['date'] == record['target_date']].set_index('item_code')
            for code in item_codes:
                actual = actual_items.loc[code, 'consumption']
                rows.append({'origin': origin, 'target_date': record['target_date'], 'horizon_day': record['horizon_day'], 'item_code': code, 'window_days': window_days, 'share_available_end': last_available_date, 'past_recorded_share': float(shares[code]), 'prediction': record['prediction_nonnegative'] * float(shares[code]), 'actual_consumption': actual, 'actual_recorded_count': int(actual_items.loc[code, 'recorded_count'])})
    allocation = pd.DataFrame(rows)
    if allocation.empty:
        return allocation, None
    score = float((allocation['prediction'] - allocation['actual_consumption']).abs().dropna().mean())
    return allocation, score if np.isfinite(score) else None


def write_arima_result_report(run_directory: Path, series: pd.Series, predictions: pd.DataFrame, summary: dict[str, object]) -> None:
    frame = predictions[predictions['horizon_day'] == 1]
    figure, axis = plt.subplots(figsize=(12, 4))
    axis.plot(series.loc['2024':].index, series.loc['2024':].values, color='gray', alpha=.45, label='Recorded group count')
    axis.plot(frame['target_date'], frame['prediction_nonnegative'], label='ARIMA: Monday day-1 forecast')
    axis.plot(frame['target_date'], frame['naive_prediction'], alpha=.7, label='Weekly seasonal naive')
    axis.set_ylabel('Recorded capsules / day')
    axis.legend()
    figure.tight_layout()
    figure.savefig(run_directory / 'forecast_2024.png', dpi=150)
    plt.close(figure)
    metadata = summary['dataset']
    final_metrics = summary['evaluation_metrics']
    metric_table = '\n'.join(f"| {span} | {final_metrics[f'first_{span}_days']['mean_daily_mae']:.4f} | {final_metrics[f'first_{span}_days']['naive_mean_daily_mae']:.4f} | {100 * final_metrics[f'first_{span}_days']['skill_vs_final_naive']:.2f}% |" for span in [1, 7, 14]) if final_metrics['status'] == 'evaluated' else '| 미평가 | — | — | — |'
    member_table = '\n'.join(f"| {member['item_code']} | {member['hospital_name']} | {member['base_unit']} |" for member in metadata['group_members'])
    report = f'''# 천안의료원 단일 그룹 ARIMA 탐색 실행 결과

- 실행 ID: {summary['run_id']}
- 계획: v1.8의 ARIMA 경로를 먼저 구현. 연속 완전 관측·역사적 메타데이터 확인 전 **native-missing 탐색 실험**이다. 정식 주 실험 완료가 아니다.
- 상태: {summary['status']}
- 그룹: {metadata['group_key']}
- 선정 모델: ARIMA{tuple(summary['selected_order'])}, 월별 재적합·Expanding, 월요일 원점, H=14.
- 선정 근거: 2023년 1–6월 완료 공통 원점의 14일 일별 MAE. 2024년 성적으로 선정하지 않았다.

## 그룹과 자료

| 병원 코드 | 품목명 | 기본 단위 |
|---|---|---|
{member_table}

두 코드는 같은 외부 제품에 연결된 행정 코드다. 서로 다른 제네릭의 전환을 확인한 결과가 아니다. ATC·성분·함량·제품 정체성·명시된 단위만으로 선정했으며 소비량·빈도·상관은 사용하지 않았다. route는 같은 제품 정체성으로 공유되지만 경로의 별도 공식 확인은 미완료다. 현재 ATC 자료를 이용한 사후 메타데이터 분류다.

선택 그룹 사용 행 {metadata['selected_rows']:,}개를 반복 삭제 없이 집계했다. 무기록 {metadata['missing_group_days']}일은 0·평균을 넣지 않고 NaN으로 유지했다. statsmodels 상태공간 ARIMA가 누락 관측을 직접 처리했으며 원본과 집계 목표값을 대체하지 않았다. 원자료 추출 완전성·무기록 의미는 아직 확인 전이다. 양수 날짜의 목표도 자료에 기록된 행수이며 전체 실제 소모의 완전성을 입증하지 않는다.

## 최종 후향적 평가

| 범위의 앞 N일 | ARIMA 일별 MAE | 계절 단순예측 일별 MAE | 기준선 대비 개선 |
|---:|---:|---:|---:|
{metric_table}

공통 평가 원점 수: {final_metrics.get('common_origins', 0)}. 여러 원점의 목표기간이 겹치므로 독립 표본 수로 해석하지 않는다. 누적 총수요 오차·MASE·편향·원본/비음수 결과는 summary.json과 forecast_metrics.parquet에 기록했다. 음의 개선은 악화다. 같은 모델의 개발·순차 점검·최종 결과를 구분했다.

![2024년 관측 기록과 예측](forecast_2024.png)

## 시간·누출·해석

전산 반영 시각은 미확인이므로 정보 지연 {summary['config']['availability_delay_days']}일의 명시적 가정 시나리오다. 원점 이후 실제값·상품 배분을 입력하지 않았고, 월 사이에는 모수를 유지하고 과거 자료로만 상태를 갱신했다. 미래 변경·그룹 불변성 검사는 검증 기록을 참조한다. 이 가정 하의 시간 계약 검사를 실제 반영 지연 확인으로 표현하지 않는다.

7/28일 과거 기록 비율 배분 중 개발에서 선정한 창은 {summary['selected_allocation_window']}일이다. 무기록 품목의 실제 소모는 미정의로 유지하므로 개별 품목 오차는 양수 관측에 조건부이며 전체 상품 배분 정확도로 일반화하지 않는다. 상품별 recorded_count가 0이어도 실제 소모 0으로 확인한 것은 아니다. A→B→A는 합성 검사 조건이며 병원의 실제 변경 원인을 추정하지 않았다.

정식 주 실험 전 남은 항목: 추출 완전성·무기록 의미, 코드/제품 연결 업무 검토, 역사적 분류·투여경로·정보 가용시각 확인. 다른 약품·전체 물류·재고·비용 절감으로 일반화하지 않는다. 현재는 ARIMA만 실행했고 VAR·XGBoost·TimesFM 비교는 수행하지 않았다.

## 재현

[방법론과 실행 명령](../../../research/methods/ARIMA.md). 코드 src/methods/arima/, 설정 config_snapshot.json, 소스/환경 해시 manifest.json, 예측 group_predictions.parquet, 품목 배분 allocation_baseline_predictions.parquet, 모형별 월별 모수 fitted_models/를 보존했다. 학습 일별 자료와 월별 모수로 해당 원점의 상태를 다시 필터링할 수 있다. 실패는 failures.json에 저장하며 다른 결과로 대체하지 않았다.
'''
    (run_directory / 'report.md').write_text(report)


def run_and_write_arima_experiment(config_path: Path, run_id: str) -> Path:
    started = time.perf_counter()
    config = json.loads(config_path.read_text())
    run_directory = Path('runs/cheonan') / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    dataset_directory = Path('data/processed/cheonan/analysis') / run_id
    (run_directory / 'config_snapshot.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
    (run_directory / 'manifest.json').write_text(json.dumps({'status': 'running', 'run_id': run_id, 'actual_started_at_utc': datetime.now(timezone.utc).isoformat()}))
    series, dataset_metadata = build_and_write_group_dataset(Path(config['original_directory']), Path(config['catalog_path']), dataset_directory, config['zero_semantics_confirmed'])
    initial_scale = calculate_fixed_seasonal_scale(series.loc[:config['initial_end']])
    development_origins = build_completed_monday_origins(config['development_start'], config['development_end'], config['horizon_days'])
    failures = []
    trial_rows = []
    for order_values in config['candidate_orders']:
        order = tuple(order_values)
        trial = run_monthly_expanding_arima(series, development_origins, order, config['horizon_days'], config['availability_delay_days'], config['maximum_iterations'])
        metrics = calculate_group_forecast_metrics(trial.predictions, initial_scale['scale'])
        failures.extend(trial.failures)
        eligible = metrics.get('common_origins') == len(development_origins) and len(development_origins) >= config['minimum_development_vectors'] and not trial.failures
        trial_rows.append({'order': list(order), 'eligible': eligible, 'development_mae': metrics.get('first_14_days', {}).get('mean_daily_mae'), 'common_origins': metrics.get('common_origins', 0), 'fit_seconds': float(trial.fit_events['fit_seconds'].sum()) if len(trial.fit_events) else None, 'metrics': metrics})
        trial.predictions.to_parquet(run_directory / f'development_order_{order[0]}{order[1]}{order[2]}.parquet', index=False)
        print(json.dumps({'phase': 'development', 'order': list(order), 'eligible': eligible, 'mae': trial_rows[-1]['development_mae']}, ensure_ascii=False), flush=True)
    (run_directory / 'tuning_trials.json').write_text(json.dumps(trial_rows, indent=2))
    eligible_trials = [row for row in trial_rows if row['eligible']]
    if not eligible_trials:
        (run_directory / 'failures.json').write_text(json.dumps(failures, indent=2))
        raise RuntimeError('No fully successful candidate on common development origins; no fallback permitted.')
    selected = min(eligible_trials, key=lambda row: (row['development_mae'], sum(row['order']), row['fit_seconds']))
    selected_order = tuple(selected['order'])
    daily_items = pd.read_parquet(dataset_directory / 'daily_item_consumption.parquet')
    item_codes = [member['item_code'] for member in dataset_metadata['group_members']]
    development = run_monthly_expanding_arima(series, development_origins, selected_order, config['horizon_days'], config['availability_delay_days'], config['maximum_iterations'])
    failures.extend(development.failures)
    allocation_scores = []
    for window in config['allocation_window_candidates']:
        _, score = build_and_score_past_allocation(development.predictions, daily_items, item_codes, window, config['availability_delay_days'])
        allocation_scores.append({'window': window, 'conditional_positive_observation_mae': score})
    valid_scores = [row for row in allocation_scores if row['conditional_positive_observation_mae'] is not None]
    selected_window = min(valid_scores, key=lambda row: (row['conditional_positive_observation_mae'], row['window']))['window'] if valid_scores else None
    (run_directory / 'selection_frozen_before_evaluation.json').write_text(json.dumps({'selected_order': list(selected_order), 'selected_allocation_window': selected_window, 'selection_period_end': config['development_end'], 'group_hash': dataset_metadata['group_hash'], 'actual_saved_at_utc': datetime.now(timezone.utc).isoformat()}, indent=2))
    runs = {}
    metric_rows = []
    for phase in ['validation', 'evaluation']:
        origins = build_completed_monday_origins(config[f'{phase}_start'], config[f'{phase}_end'], config['horizon_days'])
        forecast_run = run_monthly_expanding_arima(series, origins, selected_order, config['horizon_days'], config['availability_delay_days'], config['maximum_iterations'], run_directory / 'fitted_models' / phase)
        runs[phase] = forecast_run
        failures.extend(forecast_run.failures)
        forecast_run.predictions.to_parquet(run_directory / f'{phase}_group_predictions.parquet', index=False)
        forecast_run.fit_events.to_json(run_directory / f'{phase}_fit_events.json', orient='records', indent=2, force_ascii=False)
        metric_rows.append({'phase': phase, 'metrics': calculate_group_forecast_metrics(forecast_run.predictions, initial_scale['scale'])})
        print(json.dumps({'phase': phase, 'metrics': metric_rows[-1]['metrics']}, ensure_ascii=False), flush=True)
    predictions = runs['evaluation'].predictions
    predictions.to_parquet(run_directory / 'group_predictions.parquet', index=False)
    fold_table = predictions[['origin', 'available_data_end', 'parameter_training_end']].drop_duplicates()
    fold_table.to_parquet(run_directory / 'folds.parquet', index=False)
    fold_table.assign(passes_cutoff=lambda frame: (frame['available_data_end'] < frame['origin']) & (frame['parameter_training_end'] <= frame['available_data_end'])).to_parquet(run_directory / 'availability_audit.parquet', index=False)
    if selected_window is not None:
        allocation, allocation_mae = build_and_score_past_allocation(predictions, daily_items, item_codes, selected_window, config['availability_delay_days'])
        allocation.to_parquet(run_directory / 'allocation_baseline_predictions.parquet', index=False)
        allocation_metrics = {'conditional_positive_observation_mae': allocation_mae, 'observed_item_target_rows': int(allocation['actual_consumption'].notna().sum()), 'unobserved_item_target_rows': int(allocation['actual_consumption'].isna().sum())}
    else:
        allocation_metrics = {'status': 'no_defined_development_allocation'}
    pd.DataFrame([{'phase': row['phase'], 'span': span, **row['metrics'][f'first_{span}_days']} for row in metric_rows if row['metrics']['status'] == 'evaluated' for span in [1, 7, 14]]).to_parquet(run_directory / 'forecast_metrics.parquet', index=False)
    code_hashes = {str(path): calculate_file_sha256(path) for path in sorted(Path('src/methods/arima').glob('*.py'))}
    summary = {'run_id': run_id, 'status': 'exploratory_completed' if not failures else 'exploratory_completed_with_recorded_failures', 'config': config, 'dataset': dataset_metadata, 'selected_order': list(selected_order), 'selected_allocation_window': selected_window, 'allocation_development_scores': allocation_scores, 'allocation_evaluation_metrics': allocation_metrics, 'seasonal_scale': initial_scale, 'development_trials': trial_rows, 'validation_metrics': metric_rows[0]['metrics'], 'evaluation_metrics': metric_rows[1]['metrics'], 'failure_count': len(failures), 'elapsed_seconds': time.perf_counter() - started, 'peak_RSS_MiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20}
    (run_directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (run_directory / 'failures.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2))
    manifest = {'status': summary['status'], 'run_id': run_id, 'plan_version': config['plan_version'], 'run_role': config['run_role'], 'dataset_manifest': str(dataset_directory / 'manifest.json'), 'dataset_manifest_sha256': calculate_file_sha256(dataset_directory / 'manifest.json'), 'code_hashes': code_hashes, 'config_sha256': calculate_file_sha256(config_path), 'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(), 'git_status': subprocess.check_output(['git', 'status', '--porcelain'], text=True), 'libraries': {name: importlib.metadata.version(name) for name in ['numpy', 'pandas', 'pyarrow', 'statsmodels', 'matplotlib']}, 'actual_completed_at_utc': datetime.now(timezone.utc).isoformat(), 'raw_data_modified': False, 'imputation_applied': False, 'native_missing_handling': True, 'claimed_full_primary_experiment': False, 'limitations': ['unverified extraction completeness and absence semantics', 'one-day availability scenario unverified', 'post-hoc external metadata; same-product administrative codes', 'route not independently verified', 'ARIMA-only exploratory experiment; not generic switching or logistics benefit validation']}
    (run_directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    write_arima_result_report(run_directory, series, predictions, summary)
    print(json.dumps({'run_directory': str(run_directory), 'status': summary['status'], 'selected_order': list(selected_order), 'elapsed_seconds': summary['elapsed_seconds']}, ensure_ascii=False), flush=True)
    return run_directory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    arguments = parser.parse_args()
    run_directory = Path('runs/cheonan') / arguments.run_id
    if run_directory.exists():
        raise FileExistsError(f'Existing experiment must not be overwritten: {run_directory}')
    try:
        run_and_write_arima_experiment(arguments.config, arguments.run_id)
    except Exception as error:
        if run_directory.exists():
            (run_directory / 'manifest.json').write_text(json.dumps({'status': 'failed', 'run_id': arguments.run_id, 'error_type': type(error).__name__, 'error': str(error), 'actual_failed_at_utc': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2))
        raise


if __name__ == '__main__':
    main()
