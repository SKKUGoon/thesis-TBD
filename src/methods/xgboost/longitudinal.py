from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.methods.arima.data import calculate_file_sha256


def read_aligned_longitudinal_predictions(run_directory: Path) -> pd.DataFrame:
    model_frames = []
    for estimator in ('xgboost', 'ridge'):
        phase_frames = []
        for phase in ('validation', 'evaluation'):
            frame = pd.read_parquet(run_directory / f'{phase}_patient_composition_{estimator}_predictions.parquet')
            frame['phase'] = phase
            phase_frames.append(frame)
        combined = pd.concat(phase_frames, ignore_index=True)
        assert not combined.duplicated(['origin', 'horizon_day']).any()
        assert (combined.available_data_end < combined.origin).all()
        assert (combined.parameter_training_end < combined.origin).all()
        combined['estimator'] = estimator
        model_frames.append(combined)
    first = model_frames[0].sort_values(['origin', 'horizon_day']).reset_index(drop=True)
    second = model_frames[1].sort_values(['origin', 'horizon_day']).reset_index(drop=True)
    assert first[['origin', 'target_date', 'horizon_day', 'actual', 'naive_prediction']].equals(second[['origin', 'target_date', 'horizon_day', 'actual', 'naive_prediction']])
    return pd.concat(model_frames, ignore_index=True)


def calculate_monthly_forecast_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (estimator, phase, month), frame in predictions.groupby(['estimator', 'phase', predictions.origin.dt.to_period('M').astype(str)]):
        for horizon in (7, 14):
            selected = frame[frame.horizon_day <= horizon]
            mae = float((selected.actual - selected.prediction_nonnegative).abs().mean())
            naive_mae = float((selected.actual - selected.naive_prediction).abs().mean())
            rows.append({'estimator': estimator, 'phase': phase, 'origin_month': month, 'horizon_days': horizon, 'origins': selected.origin.nunique(), 'target_rows': len(selected), 'mae': mae, 'naive_mae': naive_mae, 'skill': 1 - mae / naive_mae if naive_mae else np.nan})
    return pd.DataFrame(rows)


def write_longitudinal_forecast_report(run_directory: Path) -> Path:
    plt.switch_backend('Agg')
    predictions = read_aligned_longitudinal_predictions(run_directory)
    monthly = calculate_monthly_forecast_metrics(predictions)
    monthly.to_parquet(run_directory / 'longitudinal_monthly_metrics.parquet', index=False)
    daily = predictions[predictions.horizon_day <= 7].sort_values(['estimator', 'target_date'])
    assert not daily.duplicated(['estimator', 'target_date']).any()
    daily.to_parquet(run_directory / 'longitudinal_nonoverlapping_daily_predictions.parquet', index=False)
    figure, axes = plt.subplots(3, 1, figsize=(16, 10), sharey=True)
    for axis, (start, end) in zip(axes, [('2023-07-01','2023-12-31'), ('2024-01-01','2024-06-30'), ('2024-07-01','2024-12-31')]):
        for estimator, color in [('xgboost', '#d95f02'), ('ridge', '#1b9e77')]:
            selected = daily[(daily.estimator == estimator) & daily.target_date.between(start, end)].copy()
            selected = selected.set_index('target_date').reindex(pd.date_range(start, end))
            if estimator == 'xgboost':
                axis.plot(selected.index, selected.actual, color='#303030', alpha=.55, linewidth=1, label='Actual daily consumption')
            axis.plot(selected.index, selected.prediction_nonnegative, color=color, linewidth=1, label=estimator)
        axis.set_title(f'{start} to {end}: weekly origins, next 7 days; monthly expanding refit')
        axis.set_ylabel('Capsules / day')
        axis.legend(loc='upper right', ncol=3)
    figure.tight_layout()
    figure.savefig(run_directory / 'longitudinal_daily.png', dpi=160)
    plt.close(figure)
    fourteen = monthly[monthly.horizon_days == 14]
    figure, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    for estimator, color in [('xgboost', '#d95f02'), ('ridge', '#1b9e77')]:
        selected = fourteen[fourteen.estimator == estimator].sort_values('origin_month')
        axes[0].plot(selected.origin_month, selected.mae, marker='o', color=color, label=estimator)
        axes[1].plot(selected.origin_month, selected.skill * 100, marker='o', color=color, label=estimator)
        if estimator == 'xgboost':
            axes[0].plot(selected.origin_month, selected.naive_mae, linestyle='--', color='gray', label='Weekly naive')
    axes[0].set_ylabel('Mean daily MAE, next 14 days')
    axes[0].legend()
    axes[1].axhline(0, color='gray', linestyle='--')
    axes[1].set_ylabel('Improvement vs naive (%)')
    axes[1].tick_params(axis='x', rotation=45)
    figure.tight_layout()
    figure.savefig(run_directory / 'longitudinal_monthly.png', dpi=160)
    plt.close(figure)
    fits = pd.concat([pd.read_parquet(run_directory / f'{phase}_patient_composition_xgboost_fits.parquet').assign(phase=phase) for phase in ('validation','evaluation')], ignore_index=True)
    fits.to_parquet(run_directory / 'longitudinal_expanding_refits.parquet', index=False)
    rows = []
    for estimator in ('xgboost', 'ridge'):
        selected = fourteen[fourteen.estimator == estimator]
        evaluation = selected[selected.phase == 'evaluation']
        rows.append(f"| {estimator} | {int((selected.skill > 0).sum())}/{len(selected)} | {int((evaluation.skill > 0).sum())}/{len(evaluation)} | {selected.skill.min()*100:.2f}% | {selected.skill.max()*100:.2f}% |")
    table = '\n'.join(f"| {month} | {frame[frame.estimator == 'xgboost'].mae.iloc[0]:.3f} | {frame[frame.estimator == 'ridge'].mae.iloc[0]:.3f} | {frame.naive_mae.iloc[0]:.3f} | {frame[frame.estimator == 'xgboost'].skill.iloc[0]*100:.2f}% |" for month, frame in fourteen.groupby('origin_month'))
    config = json.loads((run_directory / 'config_snapshot.json').read_text())
    group_manifest = json.loads((Path(config['group_dataset']) / 'manifest.json').read_text())
    product_name = group_manifest['group_members'][0]['product_name']
    hospital_names = ' 및 '.join(member['hospital_name'] for member in group_manifest['group_members'])
    results = json.loads((run_directory / 'results.json').read_text())
    evaluation_results = [result for result in results if result['phase'] == 'evaluation']
    numerical_rows = []
    for result in evaluation_results:
        metrics = result['metrics']['first_14_days']
        numerical_rows.append(f"| {result['estimator']} | {metrics['mean_daily_mae']:.4f} | {metrics['skill_vs_final_naive']*100:.2f}% | {metrics['mean_daily_bias']:.4f} | {metrics['cumulative_demand_mae']:.4f} |")
    baseline_mae = evaluation_results[0]['metrics']['first_14_days']['naive_mean_daily_mae']
    baseline_cumulative_mae = evaluation_results[0]['metrics']['first_14_days']['naive_cumulative_demand_mae']
    xgboost_result = next(result for result in evaluation_results if result['estimator'] == 'xgboost')
    ridge_result = next(result for result in evaluation_results if result['estimator'] == 'ridge')
    report = f'''# {product_name} 소모량 예측 보고서

## 1. 대상 약품과 예측 목표

천안의료원의 **{product_name}**을 대상으로 하루에 소모되는 캡슐 수를 예측했다. 병원 품목명은 **{hospital_names}**이며, 동일 제품의 두 행정 구분으로 연결된 기록을 합산했다. 서로 다른 약품 여러 개를 예측한 결과가 아니다.

사용자와 정한 '사용기록 1행 = 캡슐 1개 소모' 정의에 따라 반복 사용 행을 모두 집계했다. 2020–2024년 해당 약품 사용기록은 {group_manifest['selected_rows']:,}행이다. 무기록 {group_manifest['missing_group_days']}일은 임의로 소모량 0으로 바꾸지 않고 미관측으로 유지했다. **이번 보고서의 예측 목표는 약품 소모량이며 물품 주문량 모델은 실행하지 않았다.**

## 2. 사용 변수

| 변수 | 구성 | 예측 시점에 사용한 범위 |
|---|---|---|
| 해당 약품의 소모기록 | 두 행정 구분별 일별 사용 행 수 | XGBoost는 직전 {xgboost_result['drug_lags']}일, Ridge는 직전 {ridge_result['drug_lags']}일 |
| 전체·남녀 환자 수 | 전체 및 성별 일별 고유 환자 수 | XGBoost는 직전 {xgboost_result['patient_lags']}일, Ridge는 직전 {ridge_result['patient_lags']}일 |
| 입원·외래 환자 수 | 일별 입원/외래 구분별 고유 환자 수 | 위와 같은 과거 환자 입력 범위 |
| 연령대별 환자 수 | 0–9세, 10–19세 등 10세 단위 | 위와 같은 과거 환자 입력 범위 |
| 진단군별 환자 수 | 주상병·부상병 각각 KCD 공식 22개 대분류 | 위와 같은 과거 환자 입력 범위 |
| 요일·주말 | 앞으로 예측할 각 날짜의 요일과 주말 여부 | 예측원점에서 이미 알려진 달력 |
| 대한민국 공휴일 | 공식 공휴일·대체/임시공휴일 및 전후일 | 예측원점까지 발표된 일정 |
| 국가 거리두기 대응기간 | 연구계획의 Boolean 변수 | 예측원점까지 발표된 시행·해제 일정 |
| 예측 거리 | 앞으로 몇 번째 날인지, 1–14일 | 예측원점에서 고정 |

환자 구성은 총 69개 일별 집계 열이다. 이는 **약품 사용기록에 등장한 환자**의 구성으로, 별도의 병원 전체 방문자 명부를 사용한 것은 아니다. 환자 반복 행은 날짜·범주별로 한 번씩 세고 약품 소모 목표에서는 모두 센다. 주상병 공란은 전체 사용 행의 0.134005%, 부상병은 21.120197%이며 별도 범주로 남겼다. 미래 실제 환자 수와 병원 상품 전환·진료·구매정책은 입력하지 않았다.

## 3. 예측 모델

**XGBoost 회귀모형**을 주 모형으로, **선형 Ridge 회귀모형**을 비교 모형으로 사용했다. 기준모형은 지난주 같은 요일의 소모량을 앞으로 반복하는 주간 계절 단순예측이다. LightGBM/CatBoost 등 다른 트리 모델은 이번에 실행하지 않았다.

XGBoost는 깊이 {config['xgboost_parameters']['max_depth']}, 학습률 {config['xgboost_parameters']['eta']}, {config['boost_rounds']}회 부스팅의 고정 설정이다. Ridge의 정규화 강도는 개발자료에서 alpha={ridge_result['ridge_alpha']}를 선택했다. 하나의 모형에 예측 거리 1–14일을 함께 입력하여 각 날짜의 소모량을 직접 예측했다. 군 내 여러 약품을 동시에 출력하는 VARX를 적합한 것은 아니다.

## 4. 피팅과 예측 절차

**Expanding window**로 2020년부터 학습자료를 누적하고, **매월 첫 월요일에 모형을 새로 적합**했다. 월 사이에는 모형 계수를 유지하면서 **매주 월요일에 전날까지의 최신 입력으로 앞으로 14일을 예측**했다.

초기 학습은 2020–2022년, 시차와 Ridge 정규화 선택은 2023년 1–6월, 절차 검증은 2023년 7–12월, 후향적 평가는 2024년이다. 약품과 환자 시차는 각각 7일/28일 후보를 독립 비교해 선택했다. 2024년 성적으로 설정을 다시 선택하지 않았다. 학습에는 해당 예측원점 전에 실제값이 완료된 라벨만 사용했다. Ridge 입력 스케일도 월별 학습자료에서만 적합했다. 원자료와 목표값에 평균을 대입하지 않았다.

학습 시작일은 {fits.drug_history_start.min().date()}로 유지했다. XGBoost 학습 행은 첫 표시 적합의 {int(fits.training_rows.iloc[0]):,}행에서 마지막 {int(fits.training_rows.iloc[-1]):,}행으로 증가했다. 이 행 수는 예측원점×예측 거리의 완료 라벨 수이며 고유 날짜 수와 같지 않다.

## 5. 장기 예측 그림

![뉴론텍캡슐100밀리그램: 18개월 일별 실제 소모량과 예측](longitudinal_daily.png)

**회색은 실제 소모량, 주황색은 XGBoost, 초록색은 Ridge**다. 2023년 7월부터 2024년 12월까지의 검증·평가 예측을 표시했다. 각 주의 앞 7일만 연결하여 같은 날짜의 중복 예측을 제거했다. 표시된 값은 사후 적합값이 아니라 그 날짜 전에 만든 예측이다. 평가값이 없는 날짜는 보간하지 않았다.

반복되는 주중·주말 패턴은 추종하지만 일부 급증과 2024년 11월의 높은 실제 소모량을 충분히 따라가지 못하는 양상이 보인다. 원인은 이 그림만으로 확정하지 않았다.

![뉴론텍캡슐100밀리그램: 월별 예측 오차와 기준모형 대비 개선](longitudinal_monthly.png)

위 월별 그래프는 각 예측원점의 **앞 14일 전체 일별 평균절대오차**를 원점 월별로 집계한 것이다. 아래 패널에서 개선율이 0보다 낮으면 기준모형보다 나쁜 달이다. 일별 그림의 앞 7일 표시와 월별 14일 평가는 서로 다른 분모다.

## 6. 예측 성능 수치

2024년의 **공통 예측원점 51개, 목표 행 714개**를 비교했다. 평균절대오차는 작을수록 좋고, 편향은 음수일 때 과소예측을 뜻한다. 14일 누적 소모량 오차는 각 예측원점에서 14일 실제값 합계와 예측 합계의 절대차를 평균한 값이다.

| 모형 | 일별 평균절대오차, 캡슐/일 | 기준모형 대비 개선 | 일별 편향, 캡슐/일 | 14일 누적 소모량 평균절대오차, 캡슐 |
|---|---:|---:|---:|---:|
| 지난주 같은 요일 기준모형 | {baseline_mae:.4f} | — | — | {baseline_cumulative_mae:.4f} |
{chr(10).join(numerical_rows)}

2024년 전체의 일별 오차에서는 XGBoost와 Ridge 모두 기준모형보다 낮았고 Ridge의 오차가 조금 더 낮았다. 그러나 월별로 항상 개선된 것은 아니다. **14일 누적 소모량 오차는 두 모형 모두 기준모형보다 컸다.** 일별 오차 개선을 보충에 필요한 누적 수요 예측 개선과 동일하게 해석하지 않는다.

| 모형 | 18개월 중 기준모형보다 나은 달 | 2024년 중 나은 달 | 최저 월별 개선율 | 최고 월별 개선율 |
|---|---:|---:|---:|---:|
{chr(10).join(rows)}

XGBoost는 2024년 1월에 기준모형보다 오차가 26.92%, 11월에는 60.68% 더 컸다. 전체 평균 개선을 모든 시점의 안정성으로 해석하지 않는다. 예측원점이 주별이고 예측기간은 14일이므로 목표 날짜가 겹치며, 714개 행을 독립 표본 수로 주장하지 않는다.

| 예측원점 월 | XGBoost MAE | Ridge MAE | 기준모형 MAE | XGBoost 개선 |
|---|---:|---:|---:|---:|
{table}

## 7. 해석 범위와 결과 보존

이 결과는 **뉴론텍캡슐100밀리그램 한 제품의 후향적 소모량 예측**이다. 2023년 하반기 검증과 2024년 평가를 연결한 그림이며 두 구간을 하나의 독립 최종평가로 취급하지 않는다. 2024년은 이미 EDA로 열람했다. 실제 전산 반영 시각은 확보되지 않았으므로 전날 사건일 집계가 이용 가능하다는 시나리오다. 당시 제품 메타데이터의 가용성과 무기록의 실제 의미는 별도로 확인해야 한다. 실제 재고·주문량·비용 절감은 검증하지 않았다.

[월별 수치](longitudinal_monthly_metrics.parquet), [일별 실제값/예측](longitudinal_nonoverlapping_daily_predictions.parquet), [월별 재적합 기록](longitudinal_expanding_refits.parquet), [실행 설정](config_snapshot.json), [원점·지표 검사](validation.json), [장기 보고서 재현 검사](longitudinal_validation.json), [공란·입력 기여 등 상세 부록](technical_appendix.md), [공식 입력 정의와 근거](../../../research/methods/STANDARD_FEATURES.md), [보고서 생성 코드](../../../src/methods/xgboost/longitudinal.py).
'''
    report_path = run_directory / 'longitudinal_report.md'
    report_path.write_text(report)
    main_report_path = run_directory / 'report.md'
    appendix_path = run_directory / 'technical_appendix.md'
    if not appendix_path.exists():
        appendix_path.write_text(main_report_path.read_text())
    main_report_path.write_text(report)
    source_files = sorted(run_directory.glob('*_patient_composition_*_predictions.parquet'))
    audit = {'source_sha256': {str(path): calculate_file_sha256(path) for path in source_files}, 'report_code_sha256': calculate_file_sha256(Path(__file__)), 'daily_unique_dates_per_estimator': daily.groupby('estimator').size().to_dict(), 'matched_model_truth': True, 'past_only_cutoffs': True, 'monthly_metric_rows': len(monthly)}
    (run_directory / 'longitudinal_validation.json').write_text(json.dumps(audit, indent=2))
    print(report_path)
    print(fourteen[['estimator','origin_month','mae','skill']].to_string(index=False))
    return report_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-directory', type=Path, required=True)
    arguments = parser.parse_args()
    write_longitudinal_forecast_report(arguments.run_directory)
