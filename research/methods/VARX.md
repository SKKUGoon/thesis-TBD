# 그룹 내 교차 자기회귀와 X 검정 방법론

사용자가 요구한 통계모형은 군 내 각 약품을 공동 예측하고 X를 반영하는 VARX다. 기존 단변량 ARIMA는 초기 기준모형으로 보존한다. VARX 적합은 아직 실행하지 않았으며, 현재는 보유 자료의 X 구성 가능성과 추가 예측력을 검사했다.

Y_t는 같은 기본 단위의 그룹 내 약품별 소모량 벡터다. 같은 약품·다른 약품의 1~k_y 지연, 환자 구분별 1~k_p 지연, 예측 대상의 요일/달력을 분리한다. k_y와 k_p는 별도로 선택한다. 우선 q=0의 VARX로 교차 AR와 X를 반영하고, 필요하면 식별·수렴·잔차 진단을 만족하는 제한된 VARMAX를 비교한다. 모든 품목을 각각 독립 SARIMAX로 적합한 것을 공동 VARX로 표현하지 않는다.

statsmodels [VAR 인터페이스](https://www.statsmodels.org/stable/generated/statsmodels.tsa.vector_ar.var_model.VAR.html)는 벡터 내생변수와 외생변수를 받으며 [VARMAX 인터페이스](https://www.statsmodels.org/stable/generated/statsmodels.tsa.statespace.varmax.VARMAX.html)는 벡터 AR/MA와 외생변수를 상태공간으로 구성한다. 라이브러리 지원은 실제 병원 벡터의 정상성·순위·관측 완전성·역사적 가용시점을 대신 검증하지 않는다.

## 이번 X 검정

[최신 결과](../../runs/cheonan/x_screening_20261007_03/report.md)를 참조한다. 직전 파일럿 이후 수행한 탐색 검사이며 확증 검정으로 표시하지 않는다.

1. 기존 그룹 목표와 일별 사용기록 고유 환자 집계를 읽고 환자 합계의 내부 일치를 확인한다. 원본 수정·평균/0 대입을 하지 않는다.
2. Y의 지연 1~7, 환자의 지연 1~28, 미래 날짜에 대해 이미 알려진 요일/연중 달력을 만든다. 전체 환자 조건과 남녀 환자 조건은 대안으로 구성한다.
3. 초기 2020–2022년의 완료 라벨과 공통 완전 입력에서 후보 블록을 검정한다. AR(1~7)을 통제하고 요일 이외 후보에는 요일도 통제한다. HAC 공분산의 공동 F 검정과 12개 검정 BH 보정으로 연관성을 기록한다. p값을 인과효과나 최종 feature 선택 규칙으로 사용하지 않는다.
4. 2023년 상반기의 공통 월요일 원점 24개에서 월별 Expanding 직접 선형모형으로 h=1/7/14 특정일을 예측한다. 학습 라벨의 대상일이 원점보다 앞선 행만 사용한다. 원점 이후 환자 실제값은 사용하지 않는다.
5. AR, AR+요일, AR+요일+전체 환자, AR+요일+남녀 환자, AR+요일+연중 달력을 비교한다. h=7/14의 한 날짜 오차를 전체 7/14일 평균으로 표시하지 않는다.
6. 미래 Y/환자 값을 변경해도 해당 원점까지 feature가 바뀌지 않음을 검사한다. 순위가 부족하면 오류를 발생시키며 열 제거·pseudo-inverse 성공으로 식별을 주장하지 않는다.
7. 원본 필요 열은 2020–2022년 6개 파일 각각 첫 8,192행만 점검한다. 표본 점검은 전체 자료 품질 검증이 아니다. 입원/외래·연령·진단별 활동 환자 집계는 아직 생성·검정하지 않았다.

직접 선형 탐색은 X의 추가 가치 검사이며 VARX 실행·모형 선택 완료가 아니다. 현재 총량 그룹의 무기록 53일, 개별 코드의 무기록, 실제 가용시각의 미확인과 현행 ATC의 사후 연결 한계는 유지된다. 2024년 평가를 이 검사에 재사용하지 않았지만 직전 파일럿에서 이미 열람했으므로 미접촉 최종 평가라고 주장하지 않는다.

## 보존 코드와 재현

- [feature_screening.py](../../src/methods/varx/feature_screening.py): 과거 입력 생성, HAC·BH 검정, 월별 개발 예측, 미래 입력 변경 검사와 결과 저장.
- [audit_columns.py](../../src/methods/varx/audit_columns.py): 초기 자료의 필요한 열 표본 점검. 환자 수준 값을 저장하지 않는다.
- [test_varx_features.py](../../tests/test_varx_features.py): 미래 환자/약품 정보 배제, 수평선별 달력, 합계/구성 공선성 검사.

```sh
.venv/bin/python -m pytest -q
OPENBLAS_NUM_THREADS=1 .venv/bin/python -m src.methods.varx.feature_screening --output runs/cheonan/NEW_UNIQUE_X_SCREENING_ID
.venv/bin/python -m src.methods.varx.audit_columns --original data/processed/cheonan/original --output runs/cheonan/NEW_UNIQUE_X_SCREENING_ID/column_audit.json
```

실행 전후 설정·소스와 환자 집계/약품 목표 해시를 저장한다. 같은 실행 경로에 덮어쓰지 않는다. 상품 분류에 소비 패턴을 넣지 않는 기존 규칙을 유지한다. 환자 feature의 분류/집계 규칙과 약품 메타데이터 그룹 구성은 분리한다.
