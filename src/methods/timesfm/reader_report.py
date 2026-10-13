from __future__ import annotations

import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.methods.timesfm.data import file_sha256
from src.methods.timesfm.professor_revision import clarify_reader_report


MEDICAL_ACTIVITY_RECOMMENDATIONS = '''## 제언

### 의료행위 내역을 활용한 물품 수량 예측 개선

**약품 처방 내역뿐 아니라 수술·처치 등 실제 의료행위 내역을 확보하면 물품 수량 예측의 정확도가 높아질 것으로 예상된다.** 같은 수의 환자가 방문하더라도 어떤 수술과 처치를 받았는지에 따라 필요한 물품의 종류와 수량이 달라질 수 있기 때문이다. 현재 천안의료원 자료에는 환자별 진단과 약품 사용 기록은 있으나, 수술·처치의 종류와 시행일시를 식별할 수 있는 의료행위 내역은 없다.

추가 자료에는 환자별 의료행위 코드·명칭·시행일시·시행 부서와 물품 사용 연결 정보를 포함하는 것이 좋다. 수술·처치 처방이나 예약 내역과 실제 시행 내역을 구분하면 취소·변경에 따른 차이도 확인할 수 있다. 예측 정확도 개선이 예상되는 우선 검토 품목은 다음과 같다.

| 예상 품목 | 추가 의료행위 및 업무 정보 | 예측 개선의 기대 근거 |
|---|---|---|
| Suture set | 봉합 처치·관련 수술의 종류와 시행 건수 | 전체 환자 수가 아닌 봉합 관련 작업량을 반영할 수 있음 |
| Dressing set 뚜껑있는 것·뚜껑없는 것 | 상처 드레싱 처치의 시행 건수와 부서 | 실제 드레싱 작업량에 따른 물품 필요량을 구분할 수 있음 |
| Forcep jar | 기구를 사용하는 수술·처치 내역과 기구 반납·멸균 기록 | 의료행위에 따른 필요량과 기구 회전·재처리 과정에 따른 출고 변동을 구분할 수 있음 |
| Cotton can(S)·Cotton can(ss) | 드레싱 등 관련 처치 내역과 불출·반납 기록 | 환자 수만으로 설명하기 어려운 처치별 물품 출고량을 반영할 수 있음 |

이 품목들과 의료행위의 연결은 병원 업무 확인이 필요한 가설이며, 추가 자료에 따른 정확도 개선을 아직 검증한 결과는 아니다. 예측에는 원점 이전에 확인된 의료행위 내역을 사용하고, 미래 수술·처치 정보는 예측 당시 확인할 수 있었던 처방·예약 내역만 사용한다. 이후에 확인된 실제 시행·취소 내역을 과거 예측 입력에 넣지 않는다. 기존 입력과 의료행위 정보를 추가한 입력을 같은 평가 기간에서 비교하여 개선 정도를 검증한다. 물품의 평가 목표는 현재와 같이 기록 출고수량이며 실제 소모량과 구분한다.
'''


def write_reader_report(root: Path, output: Path) -> Path:
    """Rewrite the canonical reader report and legends from saved forecasts only."""
    manifest=json.loads((output/'manifest.json').read_text())
    dataset=root/manifest.get('cohort_selection_audit',{}).get('verified_prior_only_dataset',manifest['config']['dataset'])
    cohort=pd.read_parquet(dataset/'frozen_cohorts.parquet')
    metrics=pd.read_parquet(output/'prediction_metrics.parquet');metrics=metrics[metrics['mode']=='joint'].copy()
    forecasts=pd.concat([pd.read_parquet(output/f'{kind}_joint_evaluation.parquet') for kind in ['drug','material']],ignore_index=True)
    policies=pd.read_parquet(output/'policy_evaluation_metrics.parquet')
    archive=output/'presentation_history/pre_reader_revision_20261008'
    archive.mkdir(parents=True,exist_ok=True)
    for name in ['report.md','drug_forecasts.png','material_forecasts.png','forecast_examples.png','policy_cost_reduction.png']:
        source=output/name
        if source.exists() and not (archive/name).exists(): shutil.copy2(source,archive/name)
    plt.rcParams['font.family']='AppleGothic';plt.rcParams['axes.unicode_minus']=False
    plotted={}
    for row in cohort.itertuples():
        plotted[row.channel]=forecasts[forecasts.channel==row.channel].sort_values('origin').drop_duplicates('target_date',keep='last').sort_values('target_date')
    def draw(axis: object, info: object) -> None:
        data=plotted[info.channel]
        if info.cohort=='material': axis.bar(data.target_date,data.actual,color='#999999',width=.8,label='실제 사용')
        else: axis.plot(data.target_date,data.actual,color='#666666',linewidth=.8,label='실제 사용')
        axis.plot(data.target_date,data.prediction,color='#e66101',linewidth=.8,label='TimesFM')
        axis.set_title(info.name);axis.legend(fontsize=8)
        axis.set_ylabel('사용기록 수' if info.cohort=='drug' else '기록 출고수량(개)',fontsize=8)
    for kind in ['drug','material']:
        selected=cohort[cohort.cohort==kind]
        fig,axes=plt.subplots(len(selected),1,figsize=(14,2.2*len(selected)),squeeze=False)
        for axis,info in zip(axes[:,0],selected.itertuples()): draw(axis,info)
        fig.tight_layout();fig.savefig(output/f'{kind}_forecasts.png',dpi=110);plt.close(fig)
    fig,axes=plt.subplots(3,2,figsize=(16,10))
    for col,kind in enumerate(['drug','material']):
        for axis,info in zip(axes[:,col],cohort[cohort.cohort==kind].head(3).itertuples()): draw(axis,info)
    fig.tight_layout();fig.savefig(output/'forecast_examples.png',dpi=150);plt.close(fig)
    observed=forecasts[forecasts.observed]
    errors=observed.assign(abs_error=(observed.prediction-observed.actual).abs()).groupby('channel').agg(abs_error=('abs_error','sum'),actual_total=('actual','sum'))
    errors['wape_percent']=100*errors.abs_error/errors.actual_total
    metrics=metrics.merge(errors[['wape_percent']],left_on='channel',right_index=True)
    mean_rows=[]
    for kind,label in [('drug','약품 20개'),('material','물품 10개'),('all','전체 30개')]:
        frame=metrics if kind=='all' else metrics[metrics.cohort==kind]
        mean_rows.append({'group':label,'items':len(frame),'macro_mase':float(frame.mase.mean()),'macro_wape_percent':float(frame.wape_percent.mean()),'observed_labels':int(frame.observations.sum()),'possible_labels':int(frame.possible_labels.sum())})
    summary_table='\n'.join(f"| {r['group']} | {r['macro_wape_percent']:.2f}% | {r['macro_mase']:.3f} | {r['observed_labels']:,}/{r['possible_labels']:,} |" for r in mean_rows)
    metric_table='\n'.join(f'| {row.name} | {row.mae:.3f} | {row.rmse:.3f} | {row.wape_percent:.2f}% | {row.mase:.3f} | {row.observations}/{row.possible_labels} |' for row in metrics.sort_values('channel').itertuples())
    target_table='\n'.join(f"| {'약품' if row.cohort=='drug' else '물품'} | {row.name} | {'제품별 사용 행 수' if row.cohort=='drug' else '기록 출고수량'} | {row.unit} | {row.selection_score:,.0f} |" for row in cohort.itertuples())
    policy_table=[];policy_summary=[]
    for kind,label in [('drug','약품'),('material','물품')]:
        subset=policies[(policies.cohort==kind)&(policies.policy=='timesfm_joint')]
        qualified=subset[subset.paired_service_target_met];improved=qualified[qualified.cost_reduction_percent>0]
        policy_summary.append(f"| {label} | {len(subset)} | {len(qualified)} | {len(improved)} | {qualified.cost_reduction_percent.mean():.2f}% |")
        for row in subset.itertuples():
            baseline=policies[(policies.channel==row.channel)&(policies.policy=='without_forecast')].iloc[0]
            policy_table.append(f"| {row.name} | {baseline.cost:,.0f} | {row.cost:,.0f} | {row.cost_reduction_percent:.2f}% | {baseline.fill_rate*100:.2f}% | {row.fill_rate*100:.2f}% | {'충족' if row.paired_service_target_met else '미달'} |")
    initial_table='\n'.join(f'| {r.name} | {r.initial_stock} | {r.days} | {r.segments} |' for r in policies[policies.policy=='timesfm_joint'].itertuples())
    pilot=pd.read_parquet(root/'runs/cheonan/boosting_two_items_20261007_01/accuracy_metrics.parquet')
    pilot=pilot[(pilot.phase=='evaluation')&(pilot.horizon==14)]
    model_names={'xgboost':'XGBoost','catboost':'CatBoost','lightgbm':'LightGBM'}
    pilot_table='\n'.join(f"| {'뉴론텍캡슐100밀리그램' if r.item=='drug' else 'Forcep jar'} | {model_names[r.model]} | {r.mae:.3f} | {r.observed_target_rows}/{r.all_target_rows} |" for r in pilot.itertuples())
    report=f'''# 천안의료원 약품·물품 수량 예측과 예측 기반 주문의 효용성 분석

## 1. 약품·물품과 예측 목표

자주 사용하는 약품과 물품의 **앞으로 14일 동안 필요한 일별 수량**을 예측하고, 그 예측을 주문에 활용했을 때 비용과 공급 충족률이 어떻게 달라지는지 분석했다.

| 실험 | 대상 요약 | 예측하는 값 |
|---|---|---|
| 기존 부스팅 실험 | 뉴론텍캡슐100밀리그램, Forcep jar | 약품의 일별 사용기록 수, 물품의 일별 기록 출고수량 |
| TimesFM 실험 | 타세놀이알·리마딘·엔테론 등을 포함한 약품 20개 | 제품별 일별 사용기록 수 |
| TimesFM 실험 | Forcep jar·Cotton can·Dressing set 등을 포함한 물품 10개 | 품목별 일별 기록 출고수량 |

약품은 사용자 제공 업무 기준에 따라 **사용기록 1행을 기본 단위 1개의 사용으로 계산**했다. 반복 기록도 모두 포함했다. 물품에는 환자별 실제 소모량이 없어 출고기록을 예측 목표로 사용했다. 출고수량은 구매 주문량이나 환자가 실제 소비한 양과 같은 의미가 아니다. 전체 이름·단위·선정 기준은 **부록 1**에 제시한다.

## 2. 사용 변수와 시간 범위

과거에 얼마나 사용했는지, 어떤 환자들이 방문했는지, 예측할 날짜가 어떤 날인지를 입력했다. 환자 자료는 사용기록에 등장한 환자의 일별 집계이며 병원 전체 내원자 명부와 동일하다고 가정하지 않았다.

### 2.1. 부스팅 모형의 입력

| 입력 | 약품: 뉴론텍 | 물품: Forcep jar |
|---|---|---|
| 과거 자체 사용량 | 이전 7일의 약품 사용기록 | 자체 출고량 지연변수는 사용하지 않음 |
| 과거 환자 구성 | 이전 28일 | 이전 28일 |
| 미래 날짜 정보 | 예측 대상일의 요일·공휴일·계절 주기 등 | 같은 변수 |
| 예측 대상 | 한 약품의 일별 수량 | 한 물품의 일별 출고수량 |

환자 구성에는 전체·남녀·입원/외래·10세 단위 연령·주상병/부상병의 KCD 대분류별 환자 수를 포함했다. 공란과 분류불가는 별도 범주로 유지했다. 날짜 정보에는 요일·주말·대한민국 공식 공휴일과 전후일·연간 주기·정부 거리두기 대응기간을 포함했다. 미래 날짜 정보는 예측 시점에 이미 알려진 정보만 사용했다.

### 2.2. TimesFM의 입력

| 입력 | 약품 20개 | 물품 10개 |
|---|---|---|
| 과거 목표 수량 | 이전 112일의 제품별 사용량 | 이전 56일의 품목별 출고수량 |
| 과거 환자 구성 | 이전 28일, 위와 같은 환자 구성 69개 집계 변수 | 같은 변수·기간 |
| 미래 날짜 정보 | 위와 같은 요일·공휴일·연간 주기·정부 대응기간 | 같은 변수 |
| 예측 출력 | 약품 20개의 앞으로 14일 수량 | 물품 10개의 앞으로 14일 수량 |

TimesFM에서는 여러 품목을 함께 입력해 각각의 수량을 동시에 예측했다. 약품 20개와 물품 10개는 별도로 처리했다. 약품과 물품의 수량을 합쳐 하나의 수요로 만들지 않았다.

**기술 설명:** 부스팅은 품목마다 하나의 목표 $y_t$를 예측했다. TimesFM은 약품의 $\\mathbf{{Y}}^d_t=(y^d_{{1,t}},\\ldots,y^d_{{20,t}})^\\top$와 물품의 $\\mathbf{{Y}}^m_t=(y^m_{{1,t}},\\ldots,y^m_{{10,t}})^\\top$를 각각 목표로 사용했다. 과거 목표 행렬과 환자·달력 변수에서 각각 `20×14`, `10×14`개의 수량을 출력했다. 본문과 부록의 TimesFM 수치는 모두 이 다변량 모형의 결과다.

### 2.3. 자료의 역할별 기간

| 기간 | 역할 |
|---|---|
| 2020–2022년 | TimesFM 대상 선정, 비교 지표의 기준 규모, 초기 재고·정책 기준 설정 |
| 2023년 상반기 | TimesFM 목표 이력 길이 28/56/112일 비교·선정 |
| 2023년 하반기 | 주문 안전재고 설정; 기존 부스팅의 개발 평가 |
| 2024년 | 예측 성능과 동결한 주문 정책 평가 |

부스팅의 학습자료는 시간이 흐르면서 늘어났다. TimesFM은 설정 선택 후 최근 과거 자료를 갱신하여 예측했다. 미래 환자 실제값은 입력하지 않았고 정부 정책·공휴일도 해당 예측 시점까지 발표된 일정만 사용했다. 병원 상품 전환·진료·구매정책은 입력에서 제외했다.

## 3. 모델

기존 부스팅 실험에는 **XGBoost·CatBoost·LightGBM**을 사용했다.

이번 30품목 실험에는 구글 연구팀의 **[TimesFM 3.0](https://github.com/google-research/timesfm)**을 사용했다. 다양한 시계열을 사전에 학습한 모델에 병원의 과거 자료와 환자·달력 변수를 제공해 미래 수량을 예측하는 방식이다.

**기술 설명:** TimesFM의 사전학습 가중치는 고정했다. 병원 자료로 모델 가중치를 추가 학습하거나 매월 다시 적합하지 않았다. 부스팅 2품목과 TimesFM 30품목은 대상과 입력 구조가 다르므로, 현재 결과만으로 두 모델 계열의 우열을 확정하지 않는다. 같은 30품목의 부스팅 재실험은 아직 수행하지 않았다.

## 4. 예측 절차

1. **대상을 정했다.** 2020–2022년 사용기록이 많은 약품 20개와 양의 출고기록이 있는 날짜가 많은 물품 10개를 선정했다. 확인된 제품 코드 연결·단위·기록 구조를 점검하고 대상과 순서를 고정했다.
2. **일별 수량과 환자 변수를 만들었다.** 약품의 반복 사용 행을 모두 세고, 물품은 원자료의 출고수량을 사용했다. 무기록 날짜는 0이나 평균으로 채우지 않고 미관측으로 남겼다.
3. **과거 자료 길이를 선정했다.** 2023년 상반기 24개 예측 시점에서 28/56/112일을 비교하여 약품 112일·물품 56일을 선택했다. 환자는 두 경우 모두 이전 28일로 고정했다.
4. **기존 부스팅은 학습자료를 확대하며 재적합했다.** 매월 첫 월요일에 그때까지 이용 가능한 자료로 다시 학습하고, 매주 월요일에 앞으로 14일을 예측했다. 이 절차는 기존 뉴론텍·Forcep jar 실험에 적용했다.
5. **TimesFM은 최근 과거 자료를 갱신해 예측했다.** 매주 월요일 시작을 기준으로 전날까지의 사용·환자 기록과 당시 알려진 달력을 입력했다. 2024년에는 51개 시점에서 앞으로 14일을 예측했다. 모델 가중치는 유지했다.
6. **관측된 실제값과 비교했다.** 무기록 날짜를 실제 사용 0으로 취급하지 않았다. 품목별 오차를 계산한 뒤 품목마다 같은 비중으로 전체 평균을 구했다.
7. **예측을 주문에 연결했다.** 2023년 하반기에 안전재고를 정하고 2024년에는 이를 바꾸지 않았다. 같은 초기 재고·납기·비용에서 예측을 활용하는 정책과 활용하지 않는 정책을 비교했다.

**기술 설명:** 부스팅은 Expanding window 학습·월별 재적합이고, TimesFM은 고정 가중치에 최근 문맥을 제공하는 추론이다. TimesFM을 Expanding 재학습이라고 부르지 않는다. 원점 이후 목표와 환자 실제값을 바꾸어도 해당 원점 입력·예측이 변하지 않는지 검사했다. TimesFM의 자동 보간 경로를 사용하지 않고 관측 마스크로 결측을 구분했다. 실제 자료 가용시각이 없어 사건일 다음 날 가용이라는 시나리오를 적용했다.

## 5. 실제값과 예측 결과

회색은 **실제 사용**, 주황색은 **TimesFM**이다. 물품의 회색 막대는 실제 사용의 직접 관측이 아니라 **기록 출고수량**을 뜻한다. 기록이 없는 날의 회색 빈칸은 사용량 0이 아니다.

![대표 약품·물품의 실제 사용과 TimesFM](forecast_examples.png)

약품 그림에서는 일별 변동과 연중 사용 수준 변화가 예측에 반영되어 있다. 물품에서는 평소 수준을 따라가지만 갑자기 크게 출고되는 날의 양을 충분히 잡지 못하는 모습이 보인다. 이 관찰은 그림에 제시한 대표 품목에 대한 설명이며 수치 평가는 전체 품목으로 수행했다.

전체 약품과 물품의 기존 그림도 같은 범례로 정리했다.

![약품 20개의 실제 사용과 TimesFM](drug_forecasts.png)

![물품 10개의 실제 사용과 TimesFM](material_forecasts.png)

**기술 설명:** 같은 날짜에 여러 예측이 있으면 그림에는 그 날짜 전에 생성한 가장 최근 예측을 하나만 표시했다. 다음 절의 지표는 51개 시점의 14일 예측 전체를 평가하므로 그림의 하루 한 예측과 집계 범위가 다르다.

## 6. 예측 수치와 해석

먼저 각 품목의 오차를 계산하고 **품목마다 같은 비중으로 평균**을 냈다. 사용량이 많은 몇 개 품목이 전체 결과를 좌우하지 않도록 했다.

- **사용 규모 대비 오차율:** 예측이 실제 사용 규모에 비해 얼마나 벗어났는지 나타낸다. 낮을수록 좋으며 예측의 성공 확률이나 정확도 퍼센트는 아니다.
- **과거 변동 대비 오차:** 오차를 해당 품목의 과거 주간 변동 규모로 나눈 값이다. 서로 다른 수량·단위의 품목을 비교하기 위한 지표이며 낮을수록 좋다.

| 대상 | 평균 사용 규모 대비 오차율 | 평균 과거 변동 대비 오차 | 실제 기록이 있는 평가행/전체 평가행 |
|---|---:|---:|---:|
{summary_table}

전체 평균은 약품과 물품을 모두 포함한 30개 품목 평균이다. 약품은 물품보다 과거 변동 대비 오차가 낮게 나타났다. 물품에서는 큰 출고량 변동을 예측하는 데 어려움이 남아 있다. 무기록이 있는 품목의 결과는 **실제로 기록된 날짜에 대한 조건부 성능**이다.

**기술 설명:** 사용 규모 대비 오차율은 품목별 $\\mathrm{{WAPE}}_i=100\\sum|\\widehat y_{{i,t}}-y_{{i,t}}|/\\sum y_{{i,t}}$를 계산한 뒤 동일 가중 평균했다. 과거 변동 대비 오차는 품목별 MASE이며 분모는 2020–2022년 관측 가능한 7일 차이의 절대평균이다. MASE 1은 그 과거 주간 변동과 같은 크기의 오차라는 뜻이며, 2024년 기준모형보다 우수하다는 뜻은 아니다.

MAE는 하루 예측이 실제값에서 평균적으로 몇 기본 단위 벗어났는지를, RMSE는 큰 오차에 더 큰 비중을 두었을 때의 오차를 나타낸다. 품목별 MAE·RMSE는 **부록 2**에 제시한다. 서로 다른 단위의 MAE를 합쳐 하나의 전체 수량 오차로 표시하지 않았다. 기존 부스팅의 개별 MAE도 부록에 수록하며 대상이 다른 실험의 평균과 직접 비교하지 않는다.

## 7. 효용성 분석

### 7.1. 비용 감소의 의미

예측의 목적은 수치를 맞히는 데서 끝나지 않는다. **필요할 때 공급하면서, 불필요하게 쌓아두는 재고와 부족을 줄이는 데 도움이 되는지** 확인하는 것이다. 이번 분석은 같은 수요 기록에 두 가지 주문 방식을 적용했다.

- **예측 미활용:** 과거 자료로 정한 고정 목표재고를 유지하고, 보유 재고와 도착 예정 주문량을 보고 필요한 만큼 보충했다.
- **TimesFM 활용:** 앞으로 필요한 양의 예측에 따라 목표재고를 바꾸고, 같은 방식으로 필요한 만큼 보충했다.

비용은 세 부분으로 나눴다. **보유비용**은 하루가 끝났을 때 남아 있는 재고에 부과한다. **부족비용**은 필요한 양을 공급하지 못한 수량에 부과한다. **발주비용**은 주문이 발생할 때마다 부과한다. 구매 단가·유효기간 폐기·실제 인건비는 자료가 없어 포함하지 않았다.

예를 들어 **타세놀이알650mg서방정**에서는 보유비용이 13,545→10,289, 부족비용이 26,450→4,150, 발주비용이 1,800→1,795로 변했다. 총비용 감소의 주된 부분은 **공급하지 못한 양에 대한 부족비용 감소**였다. 공급 충족률도 97.79%→99.65%로 높아졌다.

**Forcep jar**에서는 보유비용이 5,109→6,808로 늘었지만 부족비용은 19,100→15,750, 발주비용은 1,225→1,210으로 줄었다. 총비용은 감소했지만 충족률이 93.15%→94.35%로 목표 95%에 미달했다. 따라서 이 품목을 서비스 목표를 충족한 효율 개선 사례로 판정하지 않았다.

비용을 줄이면서 필요한 양을 공급하는 것이 목표다. **양쪽 정책 모두 95% 충족률을 만족하는지** 먼저 확인한 뒤 비용 감소를 해석한다. 여기서 충족률은 '필요한 총수량 중 실제로 공급한 비율'이다.

### 7.2. 효용성 수식과 연구 근거

앞서 설명한 보유·부족·발주비용을 다음과 같이 합쳤다.

$$C_{{\\pi}}=h\\sum_t I_{{\\pi,t}}^+ + p\\sum_t U_{{\\pi,t}} + K\\sum_t\\mathbf{{1}}(q_{{\\pi,t}}>0)$$

| 기호 | 의미 |
|---|---|
| $C_\\pi$ | 해당 주문 정책의 평가기간 총비용 |
| $I_{{\\pi,t}}^+$ | 당일 종료 재고 |
| $U_{{\\pi,t}}$ | 그날 새로 공급하지 못한 수량 |
| $q_{{\\pi,t}}$ | 그날 주문한 양 |
| $h$ | 재고 기본 단위 1개를 하루 보유하는 가정 비용 |
| $p$ | 신규 부족 기본 단위 1개당 가정 비용 |
| $K$ | 주문 1회당 가정 비용 |

예측 활용으로 총비용이 얼마나 줄었는지는 다음과 같이 계산했다.

$$E=100\\left(1-\\frac{{C_{{\\mathrm{{TimesFM}}}}}}{{C_{{\\mathrm{{예측미활용}}}}}}\\right)$$

E가 양수이면 비용 감소, 음수이면 비용 증가다. 이는 **본 연구가 정의한 비용 감소율**이며 선행논문에서 제시한 고유 점수를 그대로 사용한 것은 아니다. 실제 화폐 단위가 아니라 가정 비용이다.

예측을 재고 의사결정과 연결해 평가하는 근거는 **Goltsos·Syntetos·Glock·Ioannou(2022), Inventory–forecasting: Mind the gap, European Journal of Operational Research 299(2),397–419**의 [저자기관 원문](https://orca.cardiff.ac.uk/id/eprint/143417/3/1-s2.0-S0377221721006500-main.pdf)과 **Kourentzes·Trapero·Barrow, Optimising forecasting models for inventory planning**의 [저자 공개 원문](https://kourentzes.com/forecasting/wp-content/uploads/2020/05/Kourentzes_2019_Optimising_forecasting_models_for_inventory_planning.pdf)이다. 이 논문들의 예측–재고 연결을 설계 근거로 사용했으며 실험을 그대로 재현하지 않았다.

**기술 설명:** 재고 위치는 보유 재고+미도착 주문량이고, 주문량은 목표재고에서 재고 위치를 뺀 양을 0 이상으로 올림했다. 예측 미활용 목표는 과거 관측 2일 수량 평균+선정 안전재고, TimesFM 목표는 앞으로 2일 예측 합계+선정 안전재고다. 부족은 해당 날짜의 신규 미충족 수량만 계산하고 다음 날 이월하지 않았다. 날짜별 예측 분위수를 합쳐 공동 수요 분위수로 취급하지 않았다.

### 7.3. 공통 설정

실제 재고 기록으로 초기 보유량을 확인할 수 없어 **최초 재고는 연구 가정으로 설정**했다. 같은 품목의 두 정책에는 같은 시작 재고를 적용했다. 실제 병원의 현행 발주를 재현한 것이 아니라 예측을 쓰지 않는 가정 대조 정책과 비교한 것이다.

| 설정 | 공통 적용 내용 |
|---|---|
| 평가 기간 | 2024-01-01–12-26 중 예측과 실제 기록이 함께 있는 날짜 |
| 발주 검토 | 매일 |
| 납기 | 주문 후 1일 |
| 보호기간 | 오늘·다음 날의 2일 수량 |
| 최초 재고 | 품목별 2020–2022년 연속 관측 2일 수량의 95분위수 올림 |
| 보유비용 | 기본 단위 1개·1일당 1 |
| 부족비용 | 신규 미충족 기본 단위 1개당 50 |
| 발주비용 | 발주 1회당 5 |
| 공급 목표 | 수량 충족률 95% |
| 안전재고 설정 | 2023년 하반기에 계수 0/.5/1/1.5/2/3/4 중 목표 충족 최소비용 선택 |
| 개발기간 목표 미달 | 최고 충족률 조건을 진단용으로 저장하고 미달 여부 표시 |
| 무기록 날짜 | 0으로 채우지 않음. 다음 연속 관측 구간에서 양쪽 같은 최초 재고로 재시작 |
| 평가기간 설정 변경 | 없음 |
| 단위·원화 해석 | 품목별 기본 단위와 가정 비용. 원화 절감액으로 환산하지 않음 |

물품은 관측 공백이 많아 여러 구간에서 재고를 초기화했다. 따라서 실제 연속 운영 성과가 아니라 **관측 구간에 한정한 정책 비교**다. 평균·분위수는 정책 가정에만 사용했으며 원자료의 결측을 채우지 않았다.

### 7.4. 개별 수치와 그래프

| 대상 | 평가 품목 | 양쪽 95% 충족 | 그중 비용 감소 | 양쪽 충족 품목의 평균 비용 감소율 |
|---|---:|---:|---:|---:|
{chr(10).join(policy_summary)}

약품은 서비스 목표를 양쪽 모두 충족한 14개 중 13개에서 비용이 감소했다. 물품은 양쪽 목표를 충족한 2개 중 1개에서 감소했고, 다른 1개는 비용이 늘었다. 위 평균은 **서비스 목표를 충족한 품목만의 평균**이며 전체 30개가 개선되었다는 뜻이 아니다.

| 실제 품목 이름 | 예측 미활용 비용 | TimesFM 활용 비용 | 비용 감소율 | 미활용 충족률 | TimesFM 충족률 | 양쪽 95% |
|---|---:|---:|---:|---:|---:|---|
{chr(10).join(policy_table)}

![품목별 예측 활용 비용 감소율](policy_cost_reduction.png)

녹색은 양쪽 95% 충족, 회색은 서비스 조건 미달이다. 오른쪽으로 길면 비용 감소, 왼쪽이면 비용 증가다. 회색 막대의 비용 감소를 목표 충족 효율 개선으로 해석하지 않는다.

품목별 최초 재고와 실제로 평가한 기록 구간은 다음과 같다.

| 실제 품목 이름 | 최초 재고(기본 단위) | 평가일 수 | 연속 관측 구간 수 |
|---|---:|---:|---:|
{initial_table}

## 부록 1. 실제 약품·물품과 예측 목표

선정은 2020–2022년 자료만 사용했다. 약품 선정 점수는 반복 행을 포함한 사용기록 수, 물품 점수는 양의 출고가 기록된 날짜 수다. 같은 제품으로 확인된 행정 코드만 묶었다. ATC가 같다는 이유로 임상적 대체 약품을 합치지 않았다.

| 구분 | 실제 품목 이름 | 예측 목표 | 원자료 단위 | 과거 선정 점수 |
|---|---|---|---|---:|
{target_table}

사용기록 1행=기본 단위 1개는 사용자 제공 업무 기준이며 기관 공식 확인이 완료되었다는 뜻은 아니다. 원자료 단위와 입고 포장 환산이 미확인인 품목은 기록 단위의 예측·가정 평가로 해석한다. 명칭에 포함된 함량·용량을 사용 수량에 곱하여 총량으로 바꾸지 않았다.

## 부록 2. 품목별 예측 수치

### 2.1. TimesFM

MAE·RMSE는 품목별 목표 단위의 오차다. 아래 결과는 모두 2024년의 동일 TimesFM 다변량 조건이며 분리 예측 결과를 섞지 않았다. 관측/전체는 반복된 주별 14일 평가행 수로, 고유한 날짜 수와 다르다.

| 실제 품목 이름 | MAE | RMSE | 사용 규모 대비 오차율 | 과거 변동 대비 오차 | 관측/전체 |
|---|---:|---:|---:|---:|---:|
{metric_table}

### 2.2. 기존 부스팅 2품목 실험

2024년 51개 시점의 14일 예측 결과다. 뉴론텍은 이번 TimesFM 20개 약품에 포함되지 않았으며, Forcep jar도 입력 구조가 다르므로 모델 효과만을 분리한 대조 실험이 아니다.

| 실제 품목 이름 | 모델 | MAE | 관측/전체 |
|---|---|---:|---:|
{pilot_table}

2024년은 이미 자료 탐색과 예비 모델 평가를 본 후향적 평가다. 2026년 공개 기초모델의 사전학습 자료 종료일·병원 자료 중복 여부는 미확인이다. 실제 자료 가용시각·단위 환산·추출 완전성과 실제 비용/재고/납기는 기관 확인이 필요하다. 이번 결과는 실제 운영 비용 절감을 입증한 결과가 아니다.

재현용 예측·지표·정책 원장·자료 및 코드 해시·검증 기록은 [실행 기록](manifest.json)에 연결된다. 품목 선정과 지표 집계 정정은 기존 예측값을 변경하지 않았으며 기록을 보존했다. 본 보고서 수정은 모델을 다시 학습하거나 예측·정책 결과를 재선택하지 않았다.
'''
    comparison_section = output/'seasonal_baseline_section.md'
    if comparison_section.exists():
        report = report.replace('## 7. 효용성 분석\n', comparison_section.read_text()+'## 7. 효용성 분석\n')
    report = clarify_reader_report(report, cohort, policies)
    for appendix_name in ['appendix_3_epurun_timesfm.md', 'appendix_4_seosong_timesfm.md']:
        appendix_path = output/appendix_name
        if appendix_path.exists():
            report += '\n' + appendix_path.read_text()
    report += '\n' + MEDICAL_ACTIVITY_RECOMMENDATIONS
    (output/'report.md').write_text(report)
    (output/'reader_summary.json').write_text(json.dumps(mean_rows,ensure_ascii=False,indent=2))
    manifest['reader_report']={'audience':'nontechnical','timesfm_condition':'multivariate only','metrics_averaging':'equal weight per item; macro WAPE and MASE; no cross-unit raw MAE total','forecast_labels':['실제 사용','TimesFM'],'material_label_definition':'실제 사용 legend denotes recorded issue, not verified physical consumption','code_sha256':file_sha256(Path(__file__)),'new_model_execution':False}
    manifest['output_sha256']={p.name:file_sha256(p) for p in output.iterdir() if p.suffix in ['.parquet','.png','.md']}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(mean_rows,ensure_ascii=False,indent=2))
    return output/'report.md'


if __name__=='__main__':
    root=Path.cwd();write_reader_report(root,root/'runs/cheonan/timesfm_vector_20261008_01')
