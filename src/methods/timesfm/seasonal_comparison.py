"""Compare saved forecasts with a pre-origin weekday-repeat benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from src.methods.timesfm.data import file_sha256


def compare_saved_drug_forecasts(
    forecasts: pd.DataFrame, targets: pd.DataFrame, catalogue: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return row audit and common-support item metrics without modifying inputs."""
    rows = forecasts.copy()
    if rows.duplicated(['channel', 'origin', 'target_date']).any():
        raise ValueError('Duplicate forecast keys')
    if not targets.index.is_unique or not targets.index.is_monotonic_increasing:
        raise ValueError('Target dates must be unique and sorted')
    if not (rows.input_end < rows.origin).all():
        raise ValueError('Saved model input extends beyond the origin')
    offset = (rows.target_date - rows.origin).dt.days
    if not offset.between(0, 13).all():
        raise ValueError('Expected a 14-day forecast horizon')
    rows['baseline_source_date'] = rows.origin - pd.Timedelta(days=7) + pd.to_timedelta(offset % 7, unit='D')
    if not (rows.baseline_source_date < rows.origin).all():
        raise ValueError('Future baseline input')
    rows['baseline_prediction'] = [
        targets.at[date, channel] if date in targets.index else np.nan
        for date, channel in zip(rows.baseline_source_date, rows.channel)
    ]
    actual = np.array([
        targets.at[date, channel] for date, channel in zip(rows.target_date, rows.channel)
    ])
    if not np.allclose(actual, rows.actual.to_numpy(), equal_nan=True):
        raise ValueError('Saved labels differ from frozen targets')
    rows['common_observed'] = np.isfinite(rows.actual) & np.isfinite(rows.prediction) & np.isfinite(rows.baseline_prediction)
    records = []
    for info in catalogue[catalogue.cohort.eq('drug')].itertuples():
        all_rows = rows[rows.channel.eq(info.channel)]
        common = all_rows[all_rows.common_observed]
        if common.empty or common.actual.sum() <= 0:
            records.append({
                'channel': info.channel, 'name': info.name,
                'common_rows': len(common), 'timesfm_observed_rows': int(np.isfinite(all_rows.actual).sum()),
                'possible_rows': len(all_rows), 'baseline_wape_percent': np.nan,
                'timesfm_wape_percent': np.nan, 'baseline_mae': np.nan, 'timesfm_mae': np.nan,
                'relative_error_reduction_percent': np.nan,
                'comparison_status': 'no_common_observed_labels_or_nonpositive_denominator',
            })
            continue
        baseline_error = (common.baseline_prediction - common.actual).abs()
        model_error = (common.prediction - common.actual).abs()
        baseline_wape = float(100 * baseline_error.sum() / common.actual.sum())
        model_wape = float(100 * model_error.sum() / common.actual.sum())
        records.append({
            'channel': info.channel, 'name': info.name,
            'common_rows': len(common), 'timesfm_observed_rows': int(np.isfinite(all_rows.actual).sum()),
            'possible_rows': len(all_rows), 'baseline_wape_percent': baseline_wape,
            'timesfm_wape_percent': model_wape,
            'baseline_mae': float(baseline_error.mean()), 'timesfm_mae': float(model_error.mean()),
            'relative_error_reduction_percent': float(100 * (baseline_wape-model_wape)/baseline_wape) if baseline_wape > 0 else np.nan,
            'comparison_status': 'evaluated',
        })
    return rows, pd.DataFrame(records)


def write_comparison_and_update_report(root: Path, output: Path) -> dict[str, object]:
    """Write auditable benchmark outputs and add one section to the current report."""
    manifest = json.loads((output/'manifest.json').read_text())
    dataset = root/manifest.get('cohort_selection_audit', {}).get('verified_prior_only_dataset', manifest['config']['dataset'])
    sources = [output/'drug_joint_evaluation.parquet', dataset/'daily_targets.parquet', dataset/'frozen_cohorts.parquet']
    rows, metrics = compare_saved_drug_forecasts(*(pd.read_parquet(p) for p in sources))
    baseline = float(metrics.baseline_wape_percent.mean())
    model = float(metrics.timesfm_wape_percent.mean())
    summary = {
        'items': len(metrics), 'baseline_macro_wape_percent': baseline,
        'timesfm_macro_wape_percent_common': model,
        'relative_macro_error_reduction_percent': 100*(baseline-model)/baseline,
        'better_items': int((metrics.timesfm_wape_percent < metrics.baseline_wape_percent).sum()),
        'worse_items': int((metrics.timesfm_wape_percent > metrics.baseline_wape_percent).sum()),
        'tied_items': int((metrics.timesfm_wape_percent == metrics.baseline_wape_percent).sum()),
        'common_rows': int(metrics.common_rows.sum()),
        'timesfm_observed_rows': int(metrics.timesfm_observed_rows.sum()),
        'possible_rows': len(rows), 'new_model_training': False,
        'baseline': 'Repeat the seven calendar days immediately before each origin for both forecast weeks; missing input stays missing.',
        'code_sha256': file_sha256(Path(__file__)),
        'source_sha256': {str(p.relative_to(root)): file_sha256(p) for p in sources},
    }
    rows.to_parquet(output/'drug_seasonal_baseline_comparison.parquet', index=False)
    metrics.to_parquet(output/'drug_seasonal_baseline_metrics.parquet', index=False)
    (output/'drug_seasonal_baseline_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    table = '\n'.join(
        f'| {r.name} | {r.baseline_wape_percent:.2f}% | {r.timesfm_wape_percent:.2f}% | {r.relative_error_reduction_percent:+.2f}% | {r.common_rows}/{r.timesfm_observed_rows} |'
        for r in metrics.itertuples()
    )
    section = f'''### 6.1. 지난주 요일 반복 예측과의 비교

19.82%라는 오차율의 의미를 판단하기 위해, **지난주 같은 요일에 사용한 수량을 앞으로도 그대로 사용한다고 예측하는 단순한 방법**과 비교했다. 기존 약품 20개와 TimesFM 다변량 예측을 그대로 사용했으며 새 모델을 학습하지 않았다.

| 비교 방법 | 약품 20개 평균 사용 규모 대비 오차율 |
|---|---:|
| 지난주 요일 반복 | {baseline:.2f}% |
| TimesFM | {model:.2f}% |

**TimesFM은 지난주 같은 요일의 수량을 그대로 반복한 예측보다 약품 수량을 더 정확하게 예측했다. 약품 20개의 평균 예측 오차율이 {baseline:.2f}%에서 {model:.2f}%로 낮아져, 예측 오차가 {summary['relative_macro_error_reduction_percent']:.2f}% 줄었다.** 정확도의 개선 정도는 이 예측 오차 감소로 측정했다. 두 방법은 모두 평가할 수 있는 같은 날짜에서 비교했다. 품목별로는 **{summary['better_items']}개에서 TimesFM이 더 정확했고, {summary['worse_items']}개에서는 지난주 수량 반복이 더 정확했다.** 이는 수량 예측 정확도의 비교이며 주문 비용의 감소율은 아래 효용성 분석에서 별도로 제시한다.

공통 평가행은 {summary['common_rows']:,}개다. 기존 TimesFM의 관측 평가행 {summary['timesfm_observed_rows']:,}개 중 지난주 입력을 확인할 수 없는 {summary['timesfm_observed_rows']-summary['common_rows']:,}개는 이 비교에서 제외했다. 기존 전체 관측 범위의 19.82%와 위 표의 TimesFM 오차율은 평가 범위가 달라질 수 있으므로 직접 혼합하지 않는다.

| 실제 약품 이름 | 지난주 요일 반복 WAPE | TimesFM WAPE | 상대 오차 감소율 | 공통 평가행/기존 관측 평가행 |
|---|---:|---:|---:|---:|
{table}

**기술 설명:** 원점 직전 7일의 요일별 수량을 첫째 주와 둘째 주에 반복했다. 8–14일째 예측에도 원점 이후 실제값을 사용하지 않았다. 지난주 수량이 없으면 0이나 평균으로 채우지 않고 비교 불가로 남겼다. 품목별 WAPE를 구한 뒤 동일 가중 평균했으며, 상대 감소율은 기준선과 TimesFM의 평균 WAPE 차이를 기준선 평균 WAPE로 나눈 값이다. 51개 원점의 14일 예측은 날짜가 중첩되므로 이 표를 독립된 일별 표본의 통계적 유의성 검정으로 해석하지 않는다. 단순 기준모형 비교의 근거는 [Hyndman·Athanasopoulos의 Forecasting Principles and Practice 5.2](https://otexts.com/fpp3/simple-methods.html)다.

재현용 [품목별 비교 수치](drug_seasonal_baseline_metrics.parquet), [원점·입력 날짜·예측 및 관측 마스크](drug_seasonal_baseline_comparison.parquet), [비교 설정과 요약](drug_seasonal_baseline_summary.json)을 보존했다. 주문 정책 효용성은 아래의 예측 활용 대 미활용 정책 비교를 유지한다.

'''
    fragment = output/'seasonal_baseline_section.md'
    fragment.write_text(section)
    report_path = output/'report.md'
    report = report_path.read_text()
    archive = output/'presentation_history/pre_seasonal_comparison_20261011'
    archive.mkdir(parents=True, exist_ok=True)
    if not (archive/'report.md').exists():
        shutil.copy2(report_path, archive/'report.md')
    start = '### 6.1. 지난주 요일 반복 예측과의 비교\n'
    end = '## 7. 효용성 분석\n'
    if start in report:
        before, after = report.split(start, 1)
        report = before + end + after.split(end, 1)[1]
    if report.count(end) != 1:
        raise ValueError('Report insertion anchor is ambiguous')
    report_path.write_text(report.replace(end, section+end))
    manifest['seasonal_baseline_comparison'] = summary
    manifest['output_sha256'] = {p.name:file_sha256(p) for p in output.iterdir() if p.suffix in {'.parquet', '.png', '.md'}}
    (output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_comparison_and_update_report(Path.cwd(), args.run.resolve()), ensure_ascii=False, indent=2))
