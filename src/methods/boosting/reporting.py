from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.methods.arima.data import calculate_file_sha256
from src.methods.boosting.combined_report import compose_forecast_and_policy_report
from src.methods.boosting.utility import build_nonoverlapping_provisioning_periods, calculate_provisioning_utility


MODEL_COLORS = {'xgboost':'#d95f02','catboost':'#7570b3','lightgbm':'#1b9e77'}


def validate_and_read_two_item_predictions(run_directory: Path, item_kind: str, phase: str) -> dict[str, pd.DataFrame]:
    frames = {model:pd.read_parquet(run_directory/f'{item_kind}_{model}_{phase}.parquet') for model in MODEL_COLORS}
    reference = frames['xgboost'][['origin','target_date','horizon_day','actual','naive_prediction']]
    for frame in frames.values():
        assert reference.equals(frame[reference.columns])
        assert not frame.duplicated(['origin','horizon_day']).any()
        assert (frame.training_end < frame.origin).all()
        assert (frame.input_end < frame.origin).all()
        assert np.isfinite(frame.prediction).all()
    return frames


def calculate_accuracy_rows(frames: dict[str,pd.DataFrame], item_kind: str, phase: str) -> list[dict]:
    rows = []
    for model, frame in frames.items():
        observed = frame.dropna(subset=['actual'])
        for horizon in (7,14):
            selected = observed[observed.horizon_day <= horizon]
            mae = float((selected.actual-selected.prediction).abs().mean())
            naive = float((selected.actual-selected.naive_prediction).abs().mean())
            rows.append({'item':item_kind,'phase':phase,'model':model,'horizon':horizon,'mae':mae,'naive_mae':naive,'skill_percent':100*(1-mae/naive),'bias':float((selected.prediction-selected.actual).mean()),'observed_target_rows':len(selected),'all_target_rows':int((frame.horizon_day<=horizon).sum()),'origins':frame.origin.nunique()})
    return rows


def write_named_item_forecast_plot(run_directory: Path, frames: dict[str,pd.DataFrame], item_kind: str) -> None:
    periods = [('2023-07-01','2023-12-31'),('2024-01-01','2024-06-30'),('2024-07-01','2024-12-31')]
    if item_kind == 'material':
        config = json.loads((run_directory / 'config_snapshot.json').read_text())
        patient_daily = pd.read_parquet(Path(config['patient_dataset']) / 'daily_patient_composition.parquet')
        previous_day_patients = patient_daily['total__ALL'].shift(1)
        figure, axes = plt.subplots(6, 1, figsize=(16, 13), gridspec_kw={'height_ratios':[1,2,1,2,1,2]})
    else:
        figure, axes = plt.subplots(3,1,figsize=(16,9),sharey=True)
    for number, (start,end) in enumerate(periods):
        if item_kind == 'material':
            patient_axis, axis = axes[number*2:number*2+2]
            displayed_patients = previous_day_patients.loc[start:end]
            patient_axis.plot(displayed_patients.index, displayed_patients, color='#377eb8', linewidth=.9, label='Previous-day patients in usage records')
            patient_axis.set_ylabel('Patients')
            patient_axis.set_title(f'Forcep jar: {start} to {end}')
            patient_axis.legend(loc='upper right')
            patient_axis.set_xlim(pd.Timestamp(start),pd.Timestamp(end))
        else:
            axis = axes[number]
        for model, frame in frames.items():
            selected=frame[(frame.horizon_day<=7)&frame.target_date.between(start,end)].set_index('target_date')
            assert not selected.index.duplicated().any()
            selected=selected.reindex(pd.date_range(start,end))
            if model=='xgboost':
                if item_kind == 'material':
                    observed = selected.actual.dropna()
                    axis.bar(observed.index,observed,width=.85,color='#606060',alpha=.6,label='Recorded issue quantity (ea)')
                else:
                    axis.plot(selected.index,selected.actual,color='black',alpha=.5,linewidth=1,label='Observed daily consumption')
            axis.plot(selected.index,selected.prediction,color=MODEL_COLORS[model],linewidth=.9,label=model)
        if item_kind == 'drug':
            axis.set_title(f'{start} to {end}; weekly forecasts, monthly expanding refits')
        axis.set_xlim(pd.Timestamp(start),pd.Timestamp(end))
        axis.set_ylabel('Capsules / day' if item_kind=='drug' else 'Recorded issues (ea)')
        axis.legend(ncol=4,loc='upper right')
    figure.tight_layout()
    figure.savefig(run_directory/f'{item_kind}_18months.png',dpi=150)
    plt.close(figure)


def validate_and_write_boosting_utility_report(run_directory: Path) -> Path:
    plt.switch_backend('Agg')
    manifest=json.loads((run_directory/'manifest.json').read_text())
    config=json.loads((run_directory/'config_snapshot.json').read_text())
    assert all(calculate_file_sha256(Path(path))==digest for path,digest in manifest['input_sha256'].items())
    assert all(calculate_file_sha256(Path(manifest.get('code_snapshot_paths',{}).get(path,path)))==digest for path,digest in manifest['code_sha256'].items())
    names=manifest['names']
    accuracy_rows=[]
    utility_rows=[]
    monthly_rows=[]
    for item_kind in ('drug','material'):
        combined={model:[] for model in MODEL_COLORS}
        for phase in ('validation','evaluation'):
            frames=validate_and_read_two_item_predictions(run_directory,item_kind,phase)
            accuracy_rows.extend(calculate_accuracy_rows(frames,item_kind,phase))
            for model,frame in frames.items():
                combined[model].append(frame)
                for month,selected in frame.groupby(frame.origin.dt.strftime('%Y-%m')):
                    observed=selected.dropna(subset=['actual'])
                    mae=float((observed.actual-observed.prediction).abs().mean())
                    naive=float((observed.actual-observed.naive_prediction).abs().mean())
                    monthly_rows.append({'item':item_kind,'model':model,'phase':phase,'month':month,'mae':mae,'naive_mae':naive,'skill_percent':100*(1-mae/naive)})
                if phase=='evaluation':
                    periods=build_nonoverlapping_provisioning_periods(frame,item_kind)
                    periods.to_parquet(run_directory/f'{item_kind}_{model}_utility_periods.parquet',index=False)
                    for ratio in config['underage_overage_ratios']:
                        values=calculate_provisioning_utility(periods.demand.to_numpy(),periods.provision.to_numpy(),periods.baseline_provision.to_numpy(),ratio)
                        utility_rows.append({'item':item_kind,'model':model,'underage_ratio':ratio,**values})
        write_named_item_forecast_plot(run_directory,{model:pd.concat(parts,ignore_index=True) for model,parts in combined.items()},item_kind)
    accuracy=pd.DataFrame(accuracy_rows)
    utility=pd.DataFrame(utility_rows)
    monthly=pd.DataFrame(monthly_rows)
    accuracy.to_parquet(run_directory/'accuracy_metrics.parquet',index=False)
    utility.to_parquet(run_directory/'utility_metrics.parquet',index=False)
    monthly.to_parquet(run_directory/'monthly_metrics.parquet',index=False)
    figure,axes=plt.subplots(1,2,figsize=(13,4),sharey=True)
    for axis,item_kind in zip(axes,('drug','material')):
        for model,color in MODEL_COLORS.items():
            selected=utility[(utility.item==item_kind)&(utility.model==model)]
            axis.plot(selected.underage_ratio,selected.relative_cost_reduction_percent,color=color,marker='o',label=model)
        axis.axhline(0,color='gray',linestyle='--')
        axis.set_title('Drug: nonoverlapping 14-day provision' if item_kind=='drug' else 'Material: observed daily issue provision')
        axis.set_xlabel('Assumed shortage / excess unit cost ratio')
        axis.legend()
    axes[0].set_ylabel('Scenario cost reduction vs baseline (%)')
    figure.tight_layout();figure.savefig(run_directory/'utility_comparison.png',dpi=150);plt.close(figure)
    evaluation=accuracy[(accuracy.phase=='evaluation')&(accuracy.horizon==14)]
    accuracy_table='\n'.join(f"| {names[row.item]} | {row.model} | {row.mae:.4f} | {row.naive_mae:.4f} | {row.skill_percent:.2f}% | {row.bias:.4f} | {row.observed_target_rows}/{row.all_target_rows} |" for row in evaluation.itertuples())
    utility_table='\n'.join(f"| {names[row.item]} | {row.model} | {row.underage_ratio}:1 | {row.scenario_cost:.1f} | {row.baseline_scenario_cost:.1f} | {row.relative_cost_reduction_percent:.2f}% | {row.coverage_proxy*100:.2f}% |" for row in utility.itertuples())
    quantity_table='\n'.join(f"| {names[row.item]} | {row.model} | {row.periods} | {row.shortage_proxy:.1f} | {row.excess_proxy:.1f} | {row.cycle_coverage_proxy*100:.2f}% |" for row in utility[utility.underage_ratio==1].itertuples())
    performance_lines=[]
    for item_kind in ('drug','material'):
        selected=evaluation[evaluation.item==item_kind].sort_values('mae')
        first=selected.iloc[0]
        performance_lines.append(f"- **{names[item_kind]}**: 이번 고정 설정에서 가장 낮은 일별 MAE는 {first['model']}의 {first.mae:.4f}이며 기준모형 대비 {first.skill_percent:.2f}%다. 광범위한 튜닝 또는 모델 계열의 일반적 우열을 입증한 결과는 아니다.")
    report=f'''# 뉴론텍캡슐100밀리그램과 Forcep jar의 부스팅 예측 및 공급 효용성 평가

## 1. 대상 약품과 물품

**약품은 {names['drug']}**, **물품은 {names['material']}**을 선정했다. 약품은 동일 제품의 일반/본인부담 행정 구분 사용기록을 합산하고, '1행=1캡슐' 정의로 일별 소모량을 예측했다. 물품은 단위 ea의 **기록된 출고수량**을 예측했다. 물품 출고는 실제 환자 소모나 구매 주문수량과 동일하다고 확정하지 않는다. Forcep jar의 재사용·회수 여부도 확인하지 않았다.

기존 점검 대상에서 이름을 고정했으며 이번 성적을 보고 품목을 바꾸지 않았다. 물품은 2020–2024년에 {manifest['material_observed_days']:,}일의 기록이 있다. 무기록 날짜는 NaN을 유지하고 0이나 평균을 넣지 않았다.

## 2. 사용 변수

| 변수 | 약품 모형 | 물품 모형 |
|---|---|---|
| 자체 소모기록 | 두 행정 구분별 직전 7일 사용 행 수 | 이번 환자 정보 기반 조건에서는 미사용 |
| 전체·남녀 환자 수 | 직전 28일 일별 고유 환자 집계 | 같은 입력 |
| 입원·외래별 환자 수 | 직전 28일 | 같은 입력 |
| 연령대별 환자 수 | 10세 구간별 직전 28일 | 같은 입력 |
| 진단군별 환자 수 | 주상병·부상병 KCD 공식 22개 군별 직전 28일 | 같은 입력 |
| 요일·주말·공휴일과 전후일 | 미래 목표일의 알려진 달력 | 같은 입력 |
| 연간 주기·예측 거리 | 목표일의 연간 sin/cos, 앞으로 1–14번째 날 | 같은 입력 |
| 국가 거리두기 대응기간 | 예측원점까지 공식 발표된 Boolean | 같은 입력 |

환자 구성은 약품 사용기록에 등장한 환자의 69개 일별 집계이며 병원 전체 방문자 명부가 아니다. 모든 환자 입력은 원점 전날까지다. 병원 상품 전환·진료·구매정책과 미래 실제 환자 수는 입력하지 않았다. 상세 입력과 공식 달력 근거는 [표준 입력 명세](../../../research/methods/STANDARD_FEATURES.md)에 있다.

## 3. 부스팅 모델

XGBoost, CatBoost, LightGBM 회귀모형을 사용했다. 모두 CPU 4개 스레드, 학습률 0.05, 깊이 3, 120회 부스팅을 기본 예산으로 고정했다. LightGBM은 최대 잎 8개, CatBoost는 RMSE, XGBoost는 squared-error 목적함수다. 모델별 다른 학습 구조 때문에 같은 숫자 설정을 완전히 동등한 용량으로 해석하지 않는다. 별도 광범위 튜닝 없이 공통 입력·예산의 탐색 비교를 실행했다. 버전은 {manifest['versions']}다.

기준모형은 원점 이전에 기록된 **가장 최근 같은 요일의 수량**을 반복한다. 약품 평가구간은 매일 기록이 있으므로 지난주 같은 요일 기준과 같다. 물품에는 무기록 날을 대체하지 않고 같은 요일의 최근 관측값만 이용한다. 이는 사전에 명시한 기준모형이며 모델 실패를 대신하는 fallback이 아니다.

### 다변량 출력 지원

부스팅이 여러 목표를 동시에 출력할 수 없다는 전제는 맞지 않다. [CatBoost MultiRMSE](https://catboost.ai/docs/en/concepts/loss-functions-multiregression)는 벡터 목표를 지원하며, [XGBoost 다중 출력](https://xgboost.readthedocs.io/en/stable/tutorials/multioutput.html)은 독립 target tree 또는 실험적인 vector leaf를 지원한다. 이번 머신에서 합성 목표 3개로 두 방식의 출력 형상 검사를 통과했다. LightGBM은 이번 실행에서 단일 목표 모형으로 사용했다. 이러한 지원은 VAR의 공동 동적 방정식·차수·계수 구조와 같지 않다. **이번 실자료에서는 품목별·모델별 scalar pooled 직접 예측을 실행했다.**

## 4. 피팅과 Expanding 예측

2020년부터 학습 시작을 고정하고 매월 첫 월요일에 원점 이전의 완료 라벨로 새로 적합했다. 매주 월요일에 최신 전날까지의 입력을 갱신하여 앞으로 14일의 일별 수량을 예측했다. 하나의 모형에 예측 거리 1–14일을 넣는 pooled direct 방식이다. 아직 관측되지 않은 미래 라벨은 학습에서 제외했다.

2023년 하반기는 절차 검증, 2024년은 후향적 평가다. 입력 시차는 기존 약품 개발에서 정한 7/28일을 고정했고 물품 환자 시차는 28일의 탐색 설정으로 명시했다. 새 모델 및 물품의 최적 시차라고 주장하지 않는다. 이미 2024년을 열람한 뒤 추가한 비교이므로 미접촉 확증 holdout이 아니다. 물품 학습과 평가에는 관측된 출고 라벨만 사용하고 무기록 날짜의 수요를 복원하지 않았다. 따라서 물품 결과는 **기록이 있는 날짜에 조건부인 출고 예측 평가**다.

## 5. 장기 예측 그림

### 뉴론텍캡슐100밀리그램의 일별 소모량

![약품 18개월 부스팅 예측](drug_18months.png)

### Forcep jar의 일별 기록 출고수량

![물품 18개월 부스팅 예측](material_18months.png)

약품 그림의 검정 선은 실제 소모기록이다. 물품 그림은 각 기간의 위 패널에 전날의 기록상 환자 수를 파란 선으로, 아래 패널에 실제 기록 출고수량을 회색 막대로 표시했다. 주황은 XGBoost, 보라는 CatBoost, 초록은 LightGBM 예측선이다. 환자 패널은 입력 흐름을 이해하기 위한 요약이며, 모델은 전날 환자 수 하나가 아니라 직전 28일의 전체·성별·입원외래·연령·진단군 구성을 사용한다. 따라서 해석은 과거 환자 구성으로 향후 출고수량을 예측하는 것이며, 환자 한 명당 실제 소모량이나 인과관계를 확인한 것은 아니다. 매주 예측의 앞 7일만 이어 날짜 중복을 제거했다. 물품 실제값이 없는 날짜에는 빈칸을 남겼다. 그 날짜의 예측이 있다는 사실은 실제 출고 또는 0수요가 확인됐다는 의미가 아니다.

## 6. 예측 성능

2024년 공통 51개 원점의 앞 14일 전체 일별 평균절대오차(MAE)를 평가했다. 단위는 약품 캡슐/일, 물품 ea/기록일이다. 편향은 예측−실제의 평균으로 음수이면 과소예측이다. 목표기간이 겹쳐 714개 행은 독립 표본이 아니다. 물품의 관측 평가 행 비율을 함께 표시했다.

| 대상 | 모형 | MAE | 기준모형 MAE | 기준모형 대비 개선 | 편향 | 관측 평가 행/전체 예측 행 |
|---|---|---:|---:|---:|---:|---:|
{accuracy_table}

{chr(10).join(performance_lines)}

## 7. 예측 활용 대 미활용 주문 정책의 효율 비교

연구의 주 목표는 **예측 기반 주문을 적용하면 적용하지 않는 주문 정책보다 비용·재고·부족 측면의 효율이 개선되는지** 평가하는 것이다. 현재 주문 자체의 효율성을 판정하는 연구가 아니다. 개선은 검증할 가설이며 미리 확정한 결과가 아니다.

주 비교는 예측을 쓰지 않는 고정 재고 기준 정책 또는 업무 의미가 확인된 현행 규칙과, 동일 운영 조건에서 예측을 사용해 목표재고를 갱신하는 정책이다. 두 정책의 시작 재고·납기·검토주기·포장·비용·서비스 목표를 맞추고 재고 위치를 반영해 주문한다. 비용 감소뿐 아니라 충족률·부족일·평균재고·주문횟수를 함께 보고한다. [주 비교의 정책·수식·논문·가정 정의](../../../research/methods/ORDER_POLICY_COMPARISON.md).

**현재 아래 표는 부스팅 예측과 주간 단순 예측을 비교한 단일기간 확보량 보조 분석이다. 양쪽 모두 예측을 이용하므로 예측 활용 대 미활용 주문 정책의 효율 차이를 입증하지 않는다. 주 주문 정책 비교의 동적 재고 실험은 아직 실행하지 않았다.**

### 이전 단일기간 확보량 보조 분석

### 선행논문과 적용 범위

**Cao, Y.와 Shen, Z.-J. M. (2019)**, *Quantile forecasting and data-driven inventory management under nonstationary demand*, Operations Research Letters 47(6), 465–472, [DOI:10.1016/j.orl.2019.08.008](https://doi.org/10.1016/j.orl.2019.08.008). 시계열의 분위수 예측을 뉴스벤더 재고 결정과 연결하는 근거로 사용한다. 이번 실험은 그 신경망이나 분위수 학습을 재현한 것이 아니라 **뉴스벤더의 비대칭 부족·초과 비용 구조**를 점예측의 사후 공급 효용성 평가에 적용했다.

**Goltsos, T. E., Syntetos, A. A., Glock, C. H., Ioannou, G. (2022)**, *Inventory – forecasting: Mind the gap*, European Journal of Operational Research 299(2), 397–419, [저자기관 원문](https://orca.cardiff.ac.uk/id/eprint/143417/3/1-s2.0-S0377221721006500-main.pdf). 예측 정확도와 재고 의사결정을 연결해야 한다는 근거다. 이 논문의 실제 재고 관리 효과를 이번 자료에서 재현했다고 주장하지 않는다.

### 평가 기간과 가정

약품은 격주로 고정한 서로 겹치지 않는 **14일 구간**을 사용한다. 물품은 매주 앞 7일 중 실제 출고가 기록된 **중복 없는 기록일**을 사용한다. 물품의 14일 총수요는 무기록의 의미가 미확정이므로 계산하지 않는다. 두 대상의 효용성 기간과 단위가 달라 원점수·비용을 합산하거나 직접 비교하지 않는다.

각 기간 시작에 예측량만큼 확보한다고 가정하고 이월재고·발주 고정비·실제 납기·포장단위·예산 제약은 적용하지 않는다. 이 평가는 **기록된 수량에 대한 단일기간 확보량 대리지표**다. 실제 주문이나 실제 재고 궤적의 시뮬레이션이 아니다. 물품 출고가 수요와 같다는 임시 가정도 실제 소비로 확정하지 않는다.

### 수식과 의미

실제 기록수량을 $D_j$, 기간별 예측 합계를 $\\widehat D_j$, 가정 확보량을 $Q_j$로 둔다.

$$Q_j=\\lceil\\max(0,\\widehat D_j)\\rceil$$

약품은 14일 예측을 합산한 뒤 정수 캡슐로 올림하고 물품은 기록일의 예측을 정수 ea로 올림한다. 모델 예측의 음수 제한은 명시한 출력 처리이며 원자료 보완이 아니다. 기준모형에도 동일 올림을 적용했다.

$$U_j=(D_j-Q_j)_+,\\qquad O_j=(Q_j-D_j)_+,\\qquad (x)_+=\\max(x,0)$$

$U_j$는 가정 확보량의 부족, $O_j$는 초과다. 실제 결품이나 실제 잔여재고를 관측한 값이 아니다.

$$C=\\sum_j\\{{c_u U_j+c_o O_j\\}},\\qquad c_o=1,\\quad c_u\\in\\{{1,4,9\\}}$$

$c_u$는 부족 단위당 가정 페널티, $c_o$는 초과 단위당 가정 페널티다. 병원 비용 자료가 없어 **1:1·4:1·9:1은 본 연구의 민감도 가정**이며 논문에서 가져온 병원 비용 비율이 아니다. 원화 비용으로 표시하지 않는다. 뉴스벤더 최적 임계분위수는 $\\tau=c_u/(c_u+c_o)$이나, 이번에는 그 분위수를 적합하지 않았으므로 $Q_j$를 최적 주문량이라고 부르지 않는다.

$$E=100\\left(1-\\frac{{C_{{model}}}}{{C_{{baseline}}}}\\right)$$

$E$는 같은 대상·관측 범위·가정에서 기준모형 대비 비용 감소율이다. **이 정규화 점수는 본 연구의 보고 정의**이며 특정 논문의 고유 점수식을 그대로 복제했다고 서술하지 않는다. 양수는 개선, 음수는 악화이며 기준 비용이 0이면 미정의다.

$$F=\\frac{{\\sum_j\\min(D_j,Q_j)}}{{\\sum_j D_j}},\\qquad S=\\frac{{1}}{{n}}\\sum_j\\mathbf{{1}}(Q_j\\ge D_j)$$

$F$는 기록수량 중 가정 확보량으로 충족 가능한 비율, $S$는 부족 없는 가정 기간 비율이다. 실제 fill rate 또는 임상 서비스 수준과 구분하여 **충족률 대리지표**로 보고한다. 비용 개선과 충족률을 함께 확인한다.

### 단일기간 확보량 보조 분석 결과

![가정 비용 비율별 공급 효용성](utility_comparison.png)

| 대상 | 모형 | 부족:초과 비용 비율 | 가정 비용 | 기준 가정 비용 | 비용 감소율 E | 수량 충족률 대리지표 F |
|---|---|---:|---:|---:|---:|---:|
{utility_table}

아래 부족·초과 수량은 비용 비율과 무관하며 약품은 캡슐, 물품은 ea 단위다.

| 대상 | 모형 | 중복 없는 평가 기간 수 | 부족 수량 | 초과 수량 | 부족 없는 기간 비율 S |
|---|---|---:|---:|---:|---:|
{quantity_table}

비용 비율별 순위나 음의 효용성도 그대로 보고한다. 최종평가 효용성을 보고 모델·비율·시차를 재선택하지 않았다. 안전재고 최적화·분위수 모델·실제 재고와 납기를 반영한 후속 실험이 필요하다.

## 8. 저장 결과와 검증 범위

[실행 설정](config_snapshot.json), [실행 manifest](manifest.json), [예측 성능 수치](accuracy_metrics.parquet), [월별 성능](monthly_metrics.parquet), [효용성 수치](utility_metrics.parquet), [월별 적합](fit_events.parquet), [다변량 합성 검사](multioutput_synthetic_check.json), [분석 코드](../../../src/methods/boosting/run.py), [효용성 코드](../../../src/methods/boosting/utility.py), [보고서 코드](../../../src/methods/boosting/reporting.py).

모델은 fitted_models/, 원점별 실제값과 예측은 품목_모델_구간.parquet, 효용성 기간별 확보량/실제량은 품목_모델_utility_periods.parquet에 보존했다. 원자료·무손실 Parquet를 변경하지 않았다. 입력 시점은 전날 사건일 집계가 이용 가능한 시나리오이며 실제 전산 반영 시각은 미확인이다. 효용성은 논문에 근거한 가정 평가이고 실제 병원 물류 개선 검증은 아니다.
'''
    path=run_directory/'report.md'
    combined_sources=run_directory/'combined_report_sources.json'
    if combined_sources.exists():
        policy_directory=Path(json.loads(combined_sources.read_text())['policy_directory'])
        report=compose_forecast_and_policy_report(report,(policy_directory/'report.md').read_text(),run_directory,policy_directory)
    path.write_text(report)
    validation={'matched_model_observation_masks':True,'past_feature_and_label_cutoffs':True,'source_and_code_hashes_match':True,'missing_target_imputation':False,'utility_periods_nonoverlapping':True,'metric_rows':len(accuracy),'utility_scenarios':len(utility),'report_code_sha256':calculate_file_sha256(Path(__file__))}
    (run_directory/'report_validation.json').write_text(json.dumps(validation,indent=2))
    print(evaluation.to_string(index=False));print(utility[['item','model','underage_ratio','relative_cost_reduction_percent','coverage_proxy']].to_string(index=False));print(path)
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-directory',type=Path,required=True)
    arguments=parser.parse_args();validate_and_write_boosting_utility_report(arguments.run_directory)
