# 약품·물품 부스팅 예측과 공급 효용성 방법론

현재 범위는 뉴론텍캡슐100밀리그램(가바펜틴) 한 제품과 Forcep jar 한 품목이다. 약품은 두 행정 구분의 소모량을 합산하고 물품은 기록된 출고수량을 예측한다. 물품의 재사용·회수·출고와 실제 소비 관계는 확정하지 않는다.

## 입력과 모델

XGBoost·CatBoost·LightGBM을 동일 품목·원점·관측 마스크에서 비교한다. 약품은 기존 개발에서 선정한 자체 기록 7일과 환자 구성 28일, 물품은 환자 구성 28일을 사용한다. 필수 달력·KCD·10세 연령·정부 대응기간 정의는 [표준 입력 명세](STANDARD_FEATURES.md)를 유지한다. 물품은 환자 정보 기반 조건이므로 자체 출고 시차를 이번 입력에 포함하지 않는다. 공통 lag를 고정한 소규모 예산 비교이며 각 새 모델/물품의 최적 튜닝 결과가 아니다.

초기 학습 시작은 2020-01-01로 고정하고 월 첫 월요일에 원점 이전 완료 라벨로 재적합한다. 주별 월요일에 pooled direct H=14 예측을 실행한다. 2023년 하반기는 검증, 2024년은 후향적 평가다. 원자료·무기록을 0 또는 평균으로 대입하지 않는다. 물품은 관측된 출고 라벨만 학습·평가하고 미관측 범위와 평가 행 수를 보고한다. 미관측 실제값을 요구하는 누적 총수요 지표는 계산하지 않는다.

기준모형은 원점 이전 가장 최근 관측된 같은 요일 수량이다. 미래 자료로 기준값을 보완하지 않는다. 세 모델은 learning_rate=0.05, depth=3, 120회, CPU 4threads를 기본 예산으로 고정하고 각 알고리즘의 다른 tree 구조·규제를 명시한다. 입력과 설정은 [config](../../configs/boosting_two_items.json)에 보존한다.

## 다변량 출력

[CatBoost MultiRMSE](https://catboost.ai/docs/en/concepts/loss-functions-multiregression)와 [XGBoost vector leaf](https://xgboost.readthedocs.io/en/stable/tutorials/multioutput.html)는 여러 목표 출력이 가능하다. 합성 target 3개로 이 머신의 호환을 검사했다. 실제 두 품목 비교는 scalar 모델을 품목별·방법별로 적합한 것이며 VAR 공동 동적 방정식 또는 공동 목표 학습이라고 표현하지 않는다. LightGBM은 단일 목표 조건으로 실행했다.

## 공급 효용성

Cao·Shen (2019), *Quantile forecasting and data-driven inventory management under nonstationary demand*, [DOI](https://doi.org/10.1016/j.orl.2019.08.008)의 뉴스벤더 의사결정 근거와 Goltsos 등 (2022), *Inventory – forecasting: Mind the gap*, [저자기관 원문](https://orca.cardiff.ac.uk/id/eprint/143417/3/1-s2.0-S0377221721006500-main.pdf)을 사용한다. 실제 재고 궤적이나 분위수 학습을 재현하지 않는다.

점예측으로 확보량 Q를 정하고 부족 U=max(D−Q,0), 초과 O=max(Q−D,0), 가정 비용 C=Σ(c_u U+c_o O)를 계산한다. 초과 단위 비용 1, 부족 비용 1/4/9는 본 연구의 민감도 가정이며 원화 비용·논문의 병원 계수가 아니다. E=100(1−C_model/C_baseline)는 본 연구의 정규화 보고 정의다. 충족수량 비율과 부족 없는 기간 비율을 같이 저장한다. 약품은 격주 비중복 14일, 물품은 중복 없는 관측 기록일을 사용한다. 서로 다른 기간·단위를 합산하지 않는다. 목표 재고·이월·납기·가격·포장단위가 없어 실제 발주 최적화 또는 실제 서비스 수준으로 표현하지 않는다.

## 실행과 저장

- [학습·예측 코드](../../src/methods/boosting/run.py)
- [효용성 계산](../../src/methods/boosting/utility.py)
- [그림·보고서·원점 검증](../../src/methods/boosting/reporting.py)
- [비용·미관측·미래 불변성 검사](../../tests/test_boosting_utility.py)
- [실행 보고서](../../runs/cheonan/boosting_two_items_20261007_01/report.md)

재현 명령은 `python -m src.methods.boosting.run --config configs/boosting_two_items.json --output runs/cheonan/<new_run_id>`와 `python -m src.methods.boosting.reporting --run-directory runs/cheonan/<new_run_id>`다. 기존 실행 폴더에는 새 학습을 덮어쓰지 않는다. 월별 체크포인트·원점별 실제값/예측·효용성 기간·지표·코드/입력 해시를 같은 run에 보존한다. 실험 보고서 제목·소제목은 질문형이 아닌 명사형 또는 서술형으로 작성한다.

## 주 효용성 비교의 정정

주 평가의 대조군은 주간 단순 예측이 아니라 예측을 활용하지 않는 주문 정책이다. 기존 비용 대리지표는 예측 방법 간 보조 비교이며 예측 활용의 증분 주문 효과를 입증하지 않는다. [주문 정책 비교 정의](ORDER_POLICY_COMPARISON.md)를 적용한다. 가정 기반 동적 실험을 실행했다. [정책 비교 결과](../../runs/cheonan/boosting_two_items_20261007_01/report.md)를 별도로 보고한다.
