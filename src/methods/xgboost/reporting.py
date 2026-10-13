from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.methods.arima.data import calculate_file_sha256
from src.methods.arima.forecasting import calculate_fixed_seasonal_scale, calculate_group_forecast_metrics


plt.switch_backend('Agg')


def validate_and_write_xgboost_report(run_directory: Path) -> None:
    manifest = json.loads((run_directory / 'manifest.json').read_text())
    config = manifest['config']
    results = json.loads((run_directory / 'results.json').read_text())
    patient_root = Path(config['patient_dataset'])
    group_root = Path(config['group_dataset'])
    source_patient = json.loads((patient_root / 'manifest.json').read_text())
    overall = pd.read_parquet(patient_root / 'column_missingness_overall.parquet')
    yearly = pd.read_parquet(patient_root / 'column_missingness_by_year.parquet')
    daily = pd.read_parquet(patient_root / 'daily_patient_composition.parquet')
    original_total = pd.read_csv('data/processed/cheonan/diagnostics/eda_20261006_receipt_drug/daily_patient_counts.csv', parse_dates=['date']).set_index('date')['total_patients']
    assert daily['total__ALL'].equals(original_total.astype(float).rename('total__ALL'))
    assert all(calculate_file_sha256(Path(manifest.get('source_snapshot_paths',{}).get(path,path))) == digest for path, digest in manifest['code_hashes'].items())
    assert all(calculate_file_sha256(Path(path)) == digest for path, digest in manifest['dataset_manifest_hashes'].items())
    group = pd.read_parquet(group_root / 'daily_group_consumption.parquet').set_index('date')['consumption']
    scale = calculate_fixed_seasonal_scale(group.loc[:'2022-12-31'])['scale']
    reference = None
    for row in results:
        name = f"{row['phase']}_{row['variant']}_{row['estimator']}"
        predictions = pd.read_parquet(run_directory / f'{name}_predictions.parquet')
        assert (predictions.parameter_training_end < predictions.origin).all()
        assert (predictions.available_data_end < predictions.origin).all()
        assert calculate_group_forecast_metrics(predictions, scale) == row['metrics']
        if row['phase'] == 'evaluation':
            index = predictions[['origin', 'target_date', 'actual']].reset_index(drop=True)
            if reference is None:
                reference = index
            else:
                pd.testing.assert_frame_equal(reference, index)
    variants = {'drug_calendar': '약품별 과거 기록 + 달력', 'patient_total': '+ 전체·성별 환자', 'patient_composition': '+ 진단·입원/외래·연령 구성'}
    evaluation = [row for row in results if row['phase'] == 'evaluation']
    table = '\n'.join(f"| {variants[row['variant']]} | {row['estimator']} | {row['drug_lags']} | {row['patient_lags']} | {row['metrics']['first_14_days']['mean_daily_mae']:.4f} | {100*row['metrics']['first_14_days']['skill_vs_final_naive']:.2f}% |" for row in evaluation)
    missing_table = '\n'.join(f'| {column} | {int(row.blank_rows):,} | {row.blank_percent:.6f}% |' for column, row in overall.iterrows())
    yearly_table = '\n'.join(f"| {year} | {float(frame[frame.column=='주상병코드'].blank_percent.iloc[0]):.6f}% | {float(frame[frame.column=='부상병코드'].blank_percent.iloc[0]):.6f}% |" for year, frame in yearly.groupby('year'))
    naive = evaluation[0]['metrics']['first_14_days']['naive_mean_daily_mae']
    gain_path = run_directory / 'evaluation_patient_composition_xgboost_gain.parquet'
    gain = pd.read_parquet(gain_path)
    gain['normalized_gain'] = gain['gain'] / gain.groupby('month')['gain'].transform('sum')
    gain['family'] = gain.feature.str.replace(r'_lag_\d+$', '', regex=True)
    gain_summary = gain.groupby('family')['normalized_gain'].sum().div(gain.month.nunique()).sort_values(ascending=False)
    gain_summary.rename('mean_monthly_normalized_gain').to_frame().to_parquet(run_directory/'composition_gain_by_family.parquet')
    importance_table = '\n'.join(f'| {feature} | {value:.4f} |' for feature,value in gain_summary.head(12).items())
    figure, axes = plt.subplots(1, 2, figsize=(13, 4))
    labels = []
    values = []
    for row in evaluation:
        labels.append(f"{row['estimator']} / {row['variant'].replace('patient_', '')}")
        values.append(row['metrics']['first_14_days']['mean_daily_mae'])
    axes[0].barh(labels, values)
    axes[0].axvline(naive, color='red', linestyle='--', label='Weekly naive')
    axes[0].set_xlabel('2024 mean daily MAE over 14-day vectors')
    axes[0].legend()
    origin = pd.Timestamp('2024-07-01')
    first = pd.read_parquet(run_directory/'evaluation_patient_composition_xgboost_predictions.parquet')
    selected = first[first.origin==origin]
    second = pd.read_parquet(run_directory/'evaluation_patient_composition_ridge_predictions.parquet')
    selected_ridge = second[second.origin==origin]
    axes[1].plot(selected.target_date,selected.actual,marker='o',label='Observed recorded count')
    axes[1].plot(selected.target_date,selected.prediction_nonnegative,marker='.',label='XGBoost + composition')
    axes[1].plot(selected_ridge.target_date,selected_ridge.prediction_nonnegative,marker='.',label='Ridge + composition')
    axes[1].set_title('Illustrative fixed origin: 2024-07-01')
    axes[1].tick_params(axis='x',rotation=45)
    axes[1].set_ylabel('Recorded capsules / day')
    axes[1].legend()
    figure.tight_layout()
    figure.savefig(run_directory/'comparison.png',dpi=150)
    plt.close(figure)
    standard = source_patient.get('composition_mode') == 'standard'
    composition_definition = '정부가 공개한 KCD 대분류 22개를 주상병·부상병 각각에 적용하고, 성별·입원/외래·0–9세부터의 10세 단위 연령과 공란/유효하지 않은 값 범주를 사용했다. 상세 상위 32개 진단 선정은 이 수정 조건에 사용하지 않았다. 공식 범주와 연령 경계는 고정했으며, 초기기간에 없는 범주도 정해진 열에 보존했다.' if standard else '초기기간 상위 32개 주상병 ICD3를 상세 특징으로 쓰고 모든 초기 첫 문자 그룹과 미정의 범주를 포함했다. 원본의 모든 ICD3별 집계는 별도 보존했다.'
    external_definition = '미래 대상일 요일·주말·대한민국 공휴일·공휴일 전후와 연구계획의 is_national_epidemic_distancing_response를 모든 주 후보에 포함했다. 임시/대체공휴일과 거리두기 해제는 공식 발표 다음 날부터 이용 가능한 것으로 처리하고, 원점 이전에 알려진 발표만 반영했다. 병원 상품 전환·진료·구매정책 변수는 사용하지 않았다.' if standard else '정부 정책 발표 이력 변수는 이번 조건에 포함하지 않았다.'
    report = f'''# XGBoost・선형 Ridge: 주상병별 환자 구성 예측 결과

## 2024년 결과

선정은 2023년 상반기의 앞 14일 일별 MAE로만 했다. Expanding 학습, 매월 첫 월요일 모수 재적합, 매주 월요일 14일 예측이다. 전체 평가 원점 51개/목표 행 714개를 모든 모형에서 일치시켰다. 기준선 MAE는 **{naive:.4f}개/일**이다. 이번 표는 h=14 특정일 한 개가 아니라 **앞 14일 전체의 일별 평균 절대오차**다. 평가 목표기간이 서로 겹친다.

| 입력 | 모형 | 약품 k_y | 환자 k_p | 일별 MAE | 주간 기준선 대비 개선 |
|---|---|---:|---:|---:|---:|
{table}

![모형 비교와 고정된 예측 원점 예시](comparison.png)

각 모형의 지연과 Ridge 정규화는 개발에서 따로 선택했다. 같은 lag 조합의 개발 결과도 tuning_trials.json에 보존한다. 이 한 표로 선형 모형이 원천적으로 부적절하다거나 XGBoost가 일반적으로 우월하다고 결론 내리지 않는다. composition은 진단·연령·입원/외래를 함께 추가한 조건이므로 개선을 주상병 한 변수의 효과로 귀속하지 않는다. XGBoost 깊이·rounds는 고정된 작은 탐색 예산으로 제한했다. 모형 선택을 2024년 성적으로 다시 하지 않았다.

## 공란 비율: 전체 사용 행의 정확한 집계

전체 {source_patient['record_rows']:,}행. blank는 null 또는 공백 제거 후 빈 문자열이다. 공란 비율 때문에 주상병 구성이 불가능하다는 이전 판단은 근거가 없었다. 공란과 유효하지 않은 코드/연령은 별도 범주로 유지했다.

| 원본 열 | 공란 행 | 전체 행 대비 공란 |
|---|---:|---:|
{missing_table}

| 연도 | 주상병 공란 | 부상병 공란 |
|---:|---:|---:|
{yearly_table}

환자일 {source_patient['total_patient_days']:,}개 중 유효 주상병 ICD3가 전혀 없는 비율은 {source_patient['patient_days_without_valid_main_diagnosis_percent']:.6f}%다. 이 값은 원본 행 공란 비율과 분모/정의가 다르다. 공란 0%인 열도 값의 정확성을 검증했다는 의미는 아니다.

## 실제 입력

사용기록에 등장한 일별 고유 환자를 진단군, 성별, 연령대, 입원/외래로 집계했다. {composition_definition} 일별 구성 {source_patient['feature_count']}개 열이며 선택한 1~k_p일을 모두 넣었다. 한 환자의 여러 사용 행이 같은 진단 환자 수를 늘리지 않지만 같은 날짜의 다른 진단에는 중복 소속될 수 있다. 약품 목표는 반복 행을 모두 센 기존 목표를 유지했다. 개인식별값은 집계 파일에 저장하지 않았다.

같은 제품의 병원 약품 코드 2개 각각의 과거 recorded_count와 미래에 알려진 달력/h를 입력한다. 코드별 recorded_count의 0은 기록 없음이라는 사실이며 실제 소모 0의 확인이 아니다. 기존 그룹 목표의 미관측 53일은 대입 없이 유지하고 그 라벨을 학습에서 제외한다. 환자 범주 count의 0은 그 날짜 추출 자료에서 해당 범주 환자가 없었다는 집계다. 평균 대입·원본 평활화·미래 환자 실제값 사용은 하지 않았다.

## 모형의 형태와 한계

하나의 월별 모형에 원점 특징과 h=1…14를 넣는 **pooled direct 예측**이다. 재귀 중 실제 미래 환자를 넣거나 14개 독립 모형을 실행한 것이 아니며 VARX 공동 벡터 예측과도 구분한다. 각 학습 행의 목표일은 refit 원점 이전에 완료된 것만 사용했다. Ridge 입력 스케일은 각 학습 경계에서 MaxAbs로 적합했고 원본/목표를 바꾸지 않았다.

실제 전산 반영 시각은 없어 t−1 사건일까지 가용하다는 시나리오다. 환자 수는 약품 사용기록상의 환자 구성이지 병원 전체 내원/재원 명부가 아니다. 동일일 환자·미래에 기록된 과거 방문/퇴원을 역주입하지 않았다. {external_definition} 현행 ATC로 같은 제품 행정 코드를 연결한 사후 그룹이며 제네릭 전환 검증은 아니다. 2024년은 이미 열람된 후향적 평가다. 실제 재고·물류 개선·비용 절감으로 일반화하지 않는다.

## XGBoost 사후 입력 기여

아래 값은 각 평가 월의 학습 나무에서 분할 gain을 정규화한 뒤 12개월 평균을 낸 값이다. 여러 lag를 같은 특징군으로 합산했다. 인과효과·통계적 유의성·동일 규모의 변수 제거 실험 결과가 아니다. 성능을 보고 특징을 다시 선택하지 않았다.

| 특징군 | 평균 월별 정규화 gain |
|---|---:|
{importance_table}

## 보존 결과와 코드

[실행 방법론](../../../research/methods/XGBOOST.md), [설정](config_snapshot.json), [개발 선택](tuning_trials.json), [모형별 결과](results.json), [검증](validation.json), [실행 manifest](manifest.json). 월별 모델은 fitted_models/, 실제값/예측은 evaluation_*_predictions.parquet, 전체 공란 감사는 환자 데이터셋의 column_missingness_*.parquet에 보존했다. 실행 시간 {manifest['elapsed_seconds']:.1f}초, 최대 RSS {manifest['peak_RSS_MiB']:.1f} MiB다.
'''
    if standard:
        report += '\n## 공식 근거와 고정 입력\n\n[KCD 공식 대분류](https://sri.kostat.go.kr/boardDownload.es?bid=12030&list_no=428825&seq=1), [정부 2024년 월력요항](https://eiec.kdi.re.kr/policy/materialView.do?num=239970), [거리두기 시작 캠페인](https://www.korea.kr/briefing/policyBriefingView.do?newsId=156377930), [2022-04-15 해제 발표](https://www.korea.kr/briefing/policyBriefingView.do?newsId=148900782). 연도별 공식 달력과 추가 공휴일 발표는 [표준 입력 방법론](../../../research/methods/STANDARD_FEATURES.md)에 연결했다. 정책 True/False는 기존 계획의 넓은 거리두기 대응기간이며 생물학적 감염병 발발/종식이 아니다. 2023–2024년 평가에서는 False 구간만 관측되므로 다음 감염병 예측력을 입증하지 않는다.\n'
    (run_directory/'report.md').write_text(report)
    test_result = subprocess.run(['.venv/bin/python','-m','pytest','-q'],capture_output=True,text=True)
    assert test_result.returncode==0,test_result.stdout+test_result.stderr
    validation={'status':'passed','pytest':test_result.stdout.strip(),'all_model_common_target_alignment':True,'all_parameter_label_cutoffs':True,'all_feature_event_date_cutoffs':True,'metric_recomputation_equal':True,'source_code_hashes_match':True,'daily_total_patients_match_previous_aggregate':True,'reporting_code_sha256':calculate_file_sha256(Path(__file__)),'calendar_policy_reference_sha256': {str(path):calculate_file_sha256(path) for path in Path(config.get('calendar_reference','__no_reference__')).glob('*.parquet')},'limitations':'Availability by actual ingestion timestamp and original extraction completeness are not established by these event-date checks.'}
    (run_directory/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2))
    print(json.dumps({'report':str(run_directory/'report.md'),'validation':validation['status'],'tests':validation['pytest']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-directory',type=Path,required=True)
    args=parser.parse_args()
    validate_and_write_xgboost_report(args.run_directory)
