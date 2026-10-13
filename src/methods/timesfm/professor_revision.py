"""Clarify the reader report from frozen cohort and policy results, without inference."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


SERVICE_TARGET_EXPLANATION = '''### 7.3.1. 공급 충족률 95%의 의미와 선행연구 근거

**공급 충족률은 필요한 전체 수량 중 재고에서 공급할 수 있었던 수량의 비율이다.** 예를 들어 평가기간에 총 1,000개가 필요했고 950개를 공급했다면 충족률은 95%다. 이는 주문 성공 확률, 품절이 없었던 날짜의 비율, 예측의 95% 신뢰수준과 다르다. 초기 재고에 사용한 95분위수 역시 별도의 설정이다.

**95%는 이번 예비 실험에서 동결한 비교 목표이며 병원이 승인한 운영 기준은 아니다.** 문헌 확인은 기존 설정을 사후에 설명하는 보완 작업이다. 문헌을 보고 95%를 사전에 선정했다고 표현하지 않는다.

| 선행연구 | 확인한 내용 | 이번 연구에 적용하는 범위 |
|---|---|---|
| Neve·Schmidt, Point-of-use hospital inventory management with inaccurate usage capture, Health Care Management Science 25(1), 126–145 (2022; 온라인 공개 2021), DOI: 10.1007/s10729-021-09573-1 | 수치 실험에서 최소 충족률 90%·95%·99%를 비교 | 95%가 병원 재고 연구에서 검토된 실험 수준임을 뒷받침. 다만 해당 모형은 재고 조사 주기 마지막 날의 충족률 제약을 사용하여 본 연구의 평가기간 총수량 충족률과 집계 범위가 다름 |
| Carter·Hawa·Busby, A simple and practical approach to improving the cost effectiveness of surgical inventory management, Health Systems 14(1), 31–42 (2025; 온라인 공개 2024), DOI: 10.1080/20476965.2024.2325991 | 수술 물품의 목표 총수량 충족률은 99%. 95% 미만은 정책 탐색의 하한 설정에 사용 | 수량 충족률로 비용과 공급을 함께 평가하는 근거. 95%를 수술 물품의 충분한 운영 목표라고 정당화하는 근거는 아님 |

출처: [Neve·Schmidt 원문, Service level model 및 Numerical analysis](https://pmc.ncbi.nlm.nih.gov/articles/PMC8342273/), [Carter·Hawa·Busby 원문, 2절 및 3.2절](https://pmc.ncbi.nlm.nih.gov/articles/PMC11843630/).

따라서 95%는 **문헌에서 검토된 수준을 사용한 초기 비교 시나리오**로 해석한다. 모든 약품·물품에 충분한 공급 기준이라는 뜻은 아니다. 실제 운영 목표는 품목의 임상 중요도와 병원의 허용 부족 수준을 확인해 정해야 한다. 90%·95%·99%에서 안전재고를 각각 개발기간에 선정하고 평가기간에 동결하는 민감도 실험은 후속 과제이며 아직 수행하지 않았다. 비용 계수 1·50·5도 아래 문헌에서 추정한 병원 실측 비용이 아닌 본 연구의 가정이다.

'''


def clarify_reader_report(report: str, cohort: pd.DataFrame, policies: pd.DataFrame) -> str:
    """Return report text with complete names, error explanations and policy tables."""
    report = report.replace('### 7.4. 전체 결과와 개별 비용 예시', '### 7.4. 개별 수치와 그래프')
    names = ['### 1.1. 예측 대상 전체 목록', '', '| 번호 | 약품 20개 | 물품 10개 |', '|---:|---|---|']
    drugs = cohort.loc[cohort.cohort == 'drug', 'name'].tolist()
    materials = cohort.loc[cohort.cohort == 'material', 'name'].tolist()
    assert len(drugs) == 20 and len(materials) == 10
    for index, name in enumerate(drugs):
        names.append(f'| {index + 1} | {name} | {materials[index] if index < 10 else "—"} |')
    first, rest = report.split('## 2. 사용 변수와 시간 범위', 1)
    first = first.split('### 1.1. 예측 대상 전체 목록')[0].rstrip()
    report = first + '\n\n' + '\n'.join(names) + '\n\n단위와 선정 점수는 부록 1에 수록한다.\n\n## 2. 사용 변수와 시간 범위' + rest

    start = report.index('### 6.1. 지난주 요일 반복 예측과의 비교')
    end = report.index('## 7. 효용성 분석', start)
    report = report[:start] + '''### 6.1. 지난주 요일 반복 예측과의 비교

단순 예측은 **“지난주 월요일에 사용한 양을 다음 월요일에도 사용할 것”**이라고 보는 방식이다. TimesFM은 과거 약품 수량뿐 아니라 환자 구성과 예측 대상일의 달력 정보를 함께 사용했다. 두 방법의 수량 예측을 같은 약품·날짜에서 비교했다.

| 설명 | 값 | 쉽게 해석한 의미 |
|---|---:|---|
| 지난주 요일 반복 예측의 평균 오차율 | 24.13% | 각 약품의 실제 총수량 대비 절대오차 비율을 구한 뒤 20개 평균 |
| TimesFM의 평균 오차율 | 19.71% | 같은 기준에서 TimesFM의 오차가 더 작음 |
| 오차율 차이 | 4.42%포인트 감소 | 24.13%에서 19.71%로 낮아진 차이 |
| 기존 오차 대비 감소율 | 18.32% 감소 | 기존 오차를 100으로 보면 TimesFM의 오차는 약 81.68 |
| 품목별 비교 | 개선 18개·악화 2개 | 모든 약품의 예측이 좋아진 것은 아님 |

**약품 20개 평균 예측 오차가 지난주 요일 반복보다 18.32% 줄었다.** 계산은 (기준선 오차율−TimesFM 오차율)÷기준선 오차율×100이다. 저장된 반올림 전 값 24.1295%·19.7081%에서 계산하면 18.3239%다. 표의 반올림 값으로 다시 계산할 때 생기는 작은 차이는 반올림 때문이다. 이는 예측 수량의 오차 감소이며 발주량·재고·비용의 감소율이 아니다. 정확히 맞힌 날짜가 18.32% 늘었다는 뜻도 아니다.

| 가상의 수량 예시 | 지난주 반복 예측 | 여러 변수를 활용한 예측 |
|---|---:|---:|
| 지난주 같은 요일의 사용량 | 50개 | 50개 |
| 이번 대상일의 예측 수량 | 50개 | 40개 |
| 이번 대상일의 실제 사용량이 42개였다고 가정 | 8개 오차 | 2개 오차 |

위 50·40·42개는 이해를 위한 가상 예시이며 실제 실험 결과가 아니다. 환자 구성과 과거 흐름이 달라지면 지난주 수량을 그대로 반복하는 것보다 필요량을 잘 맞힐 수 있다는 의미다. 실제 주문은 예측량을 그대로 주문하지 않고 **현재 재고·도착 예정 주문·안전재고를 함께 고려**한다. 이 단순 예측과의 비교는 7절의 예측 미활용 주문 정책과도 별개다. 실제 간호사의 주문 방식이나 병원의 현행 정책을 조사·재현한 결과는 아니다.

공통 평가행은 13,588개다. 기존 TimesFM 관측 평가행 13,710개 중 지난주 입력을 확인할 수 없는 122개는 비교에서 제외했다. 전체 관측 범위의 19.82%와 공통 범위의 19.71%는 분모가 다르다. 14일 예측에서 둘째 주도 원점 직전 주의 요일별 수량을 반복하며, 예측 후 실제 관측값을 가져오지 않는다.

''' + report[end:]

    start = report.index('### 7.1. 비용 감소의 의미')
    end = report.index('### 7.2. 효용성 수식과 연구 근거', start)
    report = report[:start] + '''### 7.1. 비용 감소의 의미와 비용 구성

**재고 관리 효율은 필요한 양을 공급하면서 재고를 보유하는 부담, 부족의 부담, 주문 업무의 부담을 얼마나 줄였는지로 평가했다.** 같은 실제 기록에 예측을 쓰는 정책과 쓰지 않는 가정 대조 정책을 적용했다. 대조 정책은 과거 자료로 정한 고정 목표재고를 유지하고, TimesFM 정책은 앞으로 필요한 2일 수량의 예측에 따라 목표재고를 갱신한다. 양쪽 모두 현재 재고와 도착 예정 주문량을 반영해 보충한다.

**아래 비용은 실제 지출한 돈이 아니라 세 가지 부담을 같은 기준으로 비교하기 위해 만든 점수다.** 약품·물품의 기본 단위 1개를 하루 보유하는 부담을 기준인 **1점**으로 놓았다. 필요한 1개를 공급하지 못하면 그 기준의 **50배인 50점**, 발주 업무가 한 번 발생하면 그 기준의 **5배인 5점**이 든다고 가정했다. 부족을 재고 보유보다 훨씬 큰 부담으로 평가한 설정이다. 원화가 아니며, 실제 병원에서 부족비용이 보유비용의 50배라고 확인한 결과도 아니다.

| 비용 구성 | 점수를 매기는 대상 | 보유 기준 1점과의 관계 | 간단한 계산 예시 |
|---|---|---:|---|
| 보유비용 | 기본 단위 1개를 하루 보유 | 기준 1점 | 하루 종료 재고 10개면 10점, 같은 양을 이틀 보유하면 20점 |
| 부족비용 | 필요한 기본 단위 1개를 당일 공급하지 못함 | 기준의 50배인 50점 | 필요한 양 12개 중 10개만 공급하면 2개 부족, 2×50=100점 |
| 발주비용 | 발주 업무 1회 | 기준의 5배인 5점 | 40개를 한 번 주문하면 5점, 두 번 나누어 주문하면 10점 |
| 구매 단가 | 약품·물품 자체의 구매 가격 | 미포함 | 실제 단가가 없어 구매비를 계산하지 않음 |

여기서 50배·5배는 **정해진 사건 하나의 부담을 기준과 비교하는 비율**이다. 보유는 1개·1일, 부족은 공급하지 못한 1개, 발주는 수량과 무관한 주문 1회에 적용한다. 따라서 50개를 한 번 주문해도 발주 점수는 5점이며 구매 가격은 여기에 포함하지 않는다.

예를 들어 어떤 하루의 종료 재고가 10개이고, 그날 신규 부족량이 2개이며, 주문을 1회 했다면 **보유 10점+부족 100점+발주 5점=총 115점**이다. 이는 계산 방식을 보여주는 가상 예시이며 실제 품목 사례의 수치가 아니다. 보고서의 23,583.37이나 13,037.77도 이 방식으로 평가기간에 누적한 점수의 품목 평균이지 해당 금액의 원화 지출이 아니다.

이 1·50·5는 병원 실측 단가를 대신한 **연구자 설정 가중치**이며 두 정책·전 품목에 동일하게 적용했다. 본 비교는 **“부족 1개를 보유 1개·1일의 50배, 발주 1회를 5배의 부담으로 볼 때 어느 정책이 더 유리한가”**에 대한 결과다. 이 가중치를 바꾸면 정책 선택과 비교 결과가 달라질 수 있다. 실제 구매비·인건비·유효기간 폐기 비용을 조사한 값이 아니며, 비용 가중치 민감도 검증도 아직 수행하지 않았다.

공급 충족률도 함께 확인한다. 총비용이 줄어도 양쪽 정책이 모두 목표 95%를 충족하지 못하면 목표 충족 효율 개선으로 판정하지 않는다. 부족은 당일 신규 미충족 수량만 계산하며 다음 날 이월하지 않는다.

''' + report[end:]
    report = report.replace(SERVICE_TARGET_EXPLANATION, '')
    report = report.replace('### 7.4. 개별 수치와 그래프', SERVICE_TARGET_EXPLANATION + '### 7.4. 개별 수치와 그래프')

    base = policies[policies.policy == 'without_forecast'].set_index('channel')
    forecast = policies[policies.policy == 'timesfm_joint'].set_index('channel')
    assert base.index.is_unique and forecast.index.is_unique
    assert set(base.index) == set(forecast.index) and (base.cost > 0).all()
    summary = ['### 7.4. 전체 결과와 개별 비용 예시', '', '**전체 30개 품목의 품목당 평균 총비용 점수는 예측 미활용 23,583.37점에서 TimesFM 활용 13,037.77점으로 감소했다. 평균 10,545.60점의 부담이 줄었다.** 아래는 각 품목의 평가기간 총비용 점수를 동일 가중 평균한 값이다. 보유 1개·1일을 1점, 부족 1개를 50점, 발주 1회를 5점으로 가정한 조건에서의 결과다. 실제 원화 비용이나 일평균 비용이 아니다.', '', '| 대상 | 예측 미활용 평균 총비용(점) | TimesFM 활용 평균 총비용(점) | 평균 비용 감소분(점) |', '|---|---:|---:|---:|']
    for kind, label in [('all', '전체 30개'), ('drug', '약품 20개'), ('material', '물품 10개')]:
        frame = forecast if kind == 'all' else forecast[forecast.cohort == kind]
        comparison = base.loc[frame.index]
        before, after = comparison.cost.mean(), frame.cost.mean()
        summary.append(f'| {label} | {before:,.2f} | {after:,.2f} | {before-after:,.2f} |')
    summary += ['', '전체 평균에는 공급 목표 미달 품목도 포함된다. 품목마다 관측 가능한 평가기간 길이가 다르므로 하루 비용의 비교가 아닌 각 평가 구간의 누계 비용 비교다. 물품의 관측 공백과 구간 재시작 가정도 유지된다.', '', '**보유·부족·발주 비용의 전후 값:** 각 비용 구성도 같은 방식으로 품목당 평가기간 누계의 평균을 계산했다.', '', '| 대상·비용 구성 | 예측 미활용 평균 비용(점) | TimesFM 활용 평균 비용(점) |', '|---|---:|---:|']
    for kind, label in [('all', '전체 30개'), ('drug', '약품 20개'), ('material', '물품 10개')]:
        frame = forecast if kind == 'all' else forecast[forecast.cohort == kind]
        comparison = base.loc[frame.index]
        for field, component in [('holding_cost', '보유'), ('shortage_cost', '부족'), ('order_cost', '발주')]:
            summary.append(f'| {label} / {component} | {comparison[field].mean():,.2f} | {frame[field].mean():,.2f} |')
    summary += ['', '전체 평균에서는 보유비용 6,379.87→5,954.43, 부족비용 15,743.33→5,626.67, 발주비용 1,460.17→1,456.67이다. 총비용 감소의 대부분은 부족비용 감소에서 발생했다. 구성별 표시 값은 반올림하여 합계에 0.01 수준의 차이가 생길 수 있다.', '', '**공급 목표 충족 여부와 품목별 감소율:** 아래 감소율은 각 품목의 감소율을 먼저 계산한 뒤 평균한 값이다. 위 평균 총비용 두 값을 나누어 구하는 감소율과는 집계 방식이 다르다.', '', '| 대상 | 전체 품목 평균 비용 감소율 | 양쪽 95% 충족 품목 | 그중 비용 감소 품목 | 양쪽 충족 품목 평균 비용 감소율 |', '|---|---:|---:|---:|---:|']
    for kind, label in [('all', '전체 30개'), ('drug', '약품 20개'), ('material', '물품 10개')]:
        frame = forecast if kind == 'all' else forecast[forecast.cohort == kind]
        qualified = frame[frame.paired_service_target_met]
        summary.append(f'| {label} | {frame.cost_reduction_percent.mean():.2f}% | {len(qualified)}/{len(frame)} | {int((qualified.cost_reduction_percent > 0).sum())}/{len(qualified)} | {qualified.cost_reduction_percent.mean():.2f}% |')
    summary += ['', '전체 30개 평균 감소율 31.92%에는 공급 목표 미달 품목도 포함된다. 양쪽 목표 충족 16개에 한정하면 평균 감소율은 28.80%다. 약품은 양쪽 충족 14개 중 13개에서 비용이 감소했고, 물품은 2개 중 1개에서 감소했다. 이 조건을 제외하고 전체 30개가 효율적으로 개선되었다고 해석하지 않는다.']
    summary += ['', '**개별 예시:** 아래 수치는 평가기간의 가정 비용 점수 누계다. 감소 사례, 목표 미달 사례, 비용 증가 사례를 함께 제시한다.', '', '| 실제 품목·정책 | 보유비용 | 부족비용 | 발주비용 | 총비용 | 공급 충족률 |', '|---|---:|---:|---:|---:|---:|']
    examples = ['drug_01', 'material_01', 'material_08']
    for channel in examples:
        for frame, label in [(base, '예측 미활용'), (forecast, 'TimesFM')]:
            row = frame.loc[channel]
            summary.append(f'| {row["name"]} / {label} | {row.holding_cost:,.0f} | {row.shortage_cost:,.0f} | {row.order_cost:,.0f} | {row.cost:,.0f} | {100 * row.fill_rate:.2f}% |')
    summary += ['', '- **타세놀이알650mg서방정:** 총비용 41,795→16,234, 61.16% 감소. 양쪽 공급 목표를 충족했다. 종료 재고 누계 13,545→10,289개·일에 보유 단가 1을 적용했다. 부족량 529→83개에 단가 50을 적용해 부족비용 26,450→4,150이 되었다. 발주 360→359회에 회당 5를 적용해 1,800→1,795가 되었다. 총 감소분 25,561 중 부족비용 감소 22,300이 약 87.24%를 차지했다.', '- **Forcep jar:** 총비용 25,434→23,768, 6.55% 감소. 보유비용은 5,109→6,808로 늘었지만 부족량 382→315개, 발주 245→242회가 줄었다. 양쪽 충족률은 93.15%·94.35%로 95% 미달이므로 목표 충족 개선 사례는 아니다.', '- **Cotton can(ss):** 총비용 2,963→3,035, 2.43% 증가. 양쪽 95%를 충족했지만 보유비용과 부족량(24→25개), 발주 횟수(57→59회)가 모두 늘었다. 예측을 사용한다고 항상 비용이 줄지는 않는다.', '', '타세놀은 연속 361일, Forcep jar와 Cotton can(ss)는 관측 구간의 245일·205일을 평가했다. 물품은 공백 이후 같은 초기 재고로 재시작했으므로 연속 운영 성과로 해석하지 않는다.', '', '**전체 30개 품목의 개별 결과**는 다음 표와 그림에 제시한다.', '']
    start = report.index('### 7.4. 개별 수치와 그래프')
    end = report.index('| 실제 품목 이름 | 예측 미활용 비용', start)
    return report[:start] + '\n'.join(summary) + '\n' + report[end:]


def revise_saved_reader_report(output: Path, dataset: Path) -> Path:
    """Read saved aggregate inputs and overwrite only the canonical Markdown report."""
    report_path = output / 'report.md'
    report = clarify_reader_report(
        report_path.read_text(),
        pd.read_parquet(dataset / 'frozen_cohorts.parquet'),
        pd.read_parquet(output / 'policy_evaluation_metrics.parquet'),
    )
    report_path.write_text(report)
    return report_path
