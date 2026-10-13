# 천안의료원 약품·물품 수량 예측과 공급 효용성 연구

현재 계획 범위는 과거 사용기록이 많은 약품 20개와 물품 약 10개의 별도 TimesFM 벡터 예측 및 같은 코호트의 부스팅·주문 효용성 비교입니다. 품목 선정에는 2020–2022년만 사용합니다. 2026-10-08 TimesFM의 공동/독립 예측과 주 조건 주문 정책 비교를 MPS로 완료했습니다. 새 코호트 부스팅 비교와 실제 운영 검증은 미완료입니다. 기존 뉴론텍·Forcep jar 결과는 예비 실험으로 보존합니다. 물품 목표는 기록 출고수량입니다.

약품 정보만으로 한 그룹을 고정하고 그 소모량을 ARIMA·VAR·XGBoost·TimesFM으로 예측·비교하는 연구입니다. 소비 패턴은 그룹 구성에 사용하지 않습니다. A→B→A 사용 상품 전환은 확인되지 않은 가능성으로 고려하고 그룹 총량과 개별 상품 배분을 구분합니다.

이번 목표는 전체 병원 물류 효율화로 가기 위한 첫 예측 연구입니다. 실제 재고·보충 정책·비용 절감 검증과 소모품 LLM 분류는 후속 범위입니다. 2026-10-07에 같은 제품의 병원 코드 두 개를 고정하여 ARIMA 탐색 실행을 완료했습니다. 무기록은 미관측으로 유지했으며, 이후 진단·입원/외래·연령 구성으로 XGBoost/Ridge 비교까지 실행했습니다. 전체 연구 비교와 실제 물류 개선 검증은 미완료입니다.

- [최신 TimesFM 30품목 통합 보고서](runs/cheonan/timesfm_vector_20261008_01/report.md)
- [기존 2품목 통합 물류 예측·주문 효율성 보고서](runs/cheonan/boosting_two_items_20261007_01/report.md)
- [부스팅·효용성 방법론](research/methods/BOOSTING.md)
- [최신 연구계획서 v1.14](research/RESEARCH_PROPOSAL.md)
- [TimesFM 사용 방법·실행 기록·재현 계약](research/methods/TIMESFM_VECTOR_PLAN.md)
- [이푸른·서송 약품 20개 TimesFM 실행 방법과 범위](research/methods/TIMESFM_EXTERNAL.md)
- [병원 확장·동일 대상 비교군·추가 데이터 후속 계획](research/FOLLOWUP_VALIDATION_PLAN.md)
- [실험 실행 순서](research/EXPERIMENT_EXECUTION.md)
- [필수 달력·10세 연령·KCD·정부 대응기간 입력](research/methods/STANDARD_FEATURES.md)
- [18개월 Expanding 시계열·월별 성능](runs/cheonan/xgboost_standard_features_20261007_01/longitudinal_report.md)
- [수정 XGBoost·Ridge 결과](runs/cheonan/xgboost_standard_features_20261007_01/report.md)
- [이전 XGBoost·환자 구성 방법론](research/methods/XGBOOST.md)
- [XGBoost·Ridge 실행 결과와 공란 감사](runs/cheonan/xgboost_patient_composition_20261007_01/report.md)
- [VARX 구조·X 검정 방법론](research/methods/VARX.md)
- [X 후보 검정 결과](runs/cheonan/x_screening_20261007_03/report.md)
- [ARIMA 방법론·보존 코드](research/methods/ARIMA.md)
- [첫 ARIMA 탐색 결과](runs/cheonan/arima_native_missing_20261007_01/report.md)
- [ATC 자료·다변량 계산 점검](research/ATC_MULTIVARIATE_ASSESSMENT.md)
- [이전 v1.5 독립 검토](research/REVIEW_V1.5.md)
- [이전 v1.6 재검토](research/REVIEW_V1.6.md)
- [정리·Git 복구 기록](research/WORKSPACE_CLEANUP.md)
- [작업 규칙](AGENTS.md)

원자료·무손실 Parquet·기존 진단은 data/에 보존하며 이전 계획은 research/history/에 있습니다. LAVAR 코드는 로컬 Git 복구 참조에 퇴역했습니다. 실행 코드는 src/methods/<방법론>/, 방법론 설명은 research/methods/에 보존합니다. 민감한 자료·실행 산출물은 공개 Git 저장소에 추가하지 않습니다.
