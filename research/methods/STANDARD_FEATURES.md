# 사용자 지정 표준 입력: 달력·과거 환자 구성·정부 대응기간

기존 입력의 누락과 임의 연령 구간을 수정한 v1.11 실행 명세다. 주 모형에는 아래 입력을 모두 포함한다. 병원 상품 전환·진료·구매정책은 연구 범위에서 제외한다.

| 입력 | 실제 구성 | 시간 계약 |
|---|---|---|
| 미래 대상일 요일 | h별 대상 날짜의 요일, 주말, 요일 dummy | 원점에서 이미 알려진 날짜 계산 |
| 대한민국 공휴일 | 정부 연간 월력요항, 임시/대체공휴일, 전후일 | 발표 다음 날부터 반영. 발표 전의 추가 휴일은 역사적 원점에 주입하지 않음 |
| 전체·남녀 환자 수 | 날짜별 고유 환자, M/F/OTHER | 원점 전날까지 1~k_p일 |
| 입원·외래별 환자 수 | 기록의 입원/외래/OTHER별 고유 환자 | 원점 전날까지; 퇴원일로 과거 재원자를 역생성하지 않음 |
| 10세 단위 연령 | 0–9, 10–19, …, 120–129세와 미정의 범주. 원자료 유효 나이 0–120 | 원점 전날까지; 평균 나이 대입 없음 |
| KCD 진단군 | 공식 22개 대분류를 주상병·부상병 각각에 적용, 공란/분류불가 별도 | 원점 전날까지; A/B 같은 군, D와 H는 범위별 분리 |
| 국가 감염병 대응기간 | 기존 계획의 is_national_epidemic_distancing_response | 시작 캠페인 2020-02-29, 해제 시행 2022-04-18. 발표 원점에 알려진 일정만 사용 |
| 군 내 약품 기록 | 두 약품 코드 각각의 1~k_y일 recorded_count | 원점 전날까지. 기록 0과 실제 소모 0 확인은 구분 |

환자 구성은 일별 69개 고정 열이다. 전체 사용기록은 9,943,624행이며 주상병 공란 0.134005%, 부상병 공란 21.120197%, 성별·나이·입원외래 공란은 0%다. 별도 미정의 범주로 보존한다. 공란으로 구성 불가능하다는 판단은 철회한다. 환자 수는 약품 사용기록에 등장한 환자이고 여러 진단/진료구분에 속할 수 있다. 같은 사람의 반복 약품 사용 행은 환자 수에는 한 번, 약품 목표에는 모두 반영한다.

## 공식 근거

- [통계청 공개 KCD8 대분류 표, PDF 22쪽](https://sri.kostat.go.kr/boardDownload.es?bid=12030&list_no=428825&seq=1). 코드 대분류 경계로 직접 매핑한다. 첫 문자나 학습기간 상위 32개 진단만으로 대분류를 대신하지 않는다.
- [2020년 월력요항, 2019-06-03](https://eiec.kdi.re.kr/policy/materialView.do?num=189080&topic=).
- [2021년 월력요항, 2020-06-15](https://www.korea.kr/news/policyNewsView.do?newsId=148873429).
- [2022년 월력요항, 2021-08-11](https://eiec.kdi.re.kr/policy/materialView.do?num=216956).
- [2023년 월력요항, 2022-06-12](https://www.korea.kr/briefing/pressReleaseView.do?newsId=156511232).
- [2024년 월력요항, 2023-06-22](https://eiec.kdi.re.kr/policy/materialView.do?num=239970).
- [2020-08-17 임시공휴일, 2020-07-21 발표](https://www.korea.kr/news/policyNewsView.do?newsId=148874895).
- [2021년 대체공휴일 확대, 2021-08-03 발표](https://www.korea.kr/briefing/presidentView.do?newsId=148891132).
- [2023년 부처님오신날 등 확대, 2023-05-02 발표](https://www.korea.kr/briefing/stateCouncilView.do?newsId=148914550).
- [2023-10-02 임시공휴일, 2023-09-05 발표](https://www.korea.kr/news/policyNewsView.do?newsId=148919801).
- [2024-10-01 임시공휴일, 2024-09-03 발표 확인](https://www.korea.kr/news/policyNewsView.do?newsId=156648663).
- [2020-02-29 거리두기 당부 공식 브리핑](https://www.korea.kr/briefing/policyBriefingView.do?newsId=156377930), [2022-04-15 해제 발표, 4월18일 시행](https://www.korea.kr/briefing/policyBriefingView.do?newsId=148900782).

holidays 0.106으로 연도별 날짜를 생성하고 공식 연간 일요일 포함 적색표기일과 추가/대체공휴일을 대조했다. 최종 적색표기일은 2020년 68일, 2021년 67일, 2022년 67일, 2023년 69일, 2024년 69일이다. 발표일의 정확한 시각은 추정하지 않고 다음 날 00:00부터 가용한 것으로 보수적으로 설정했다. 원점 전에 알려진 미래 요일·공휴일은 사용할 수 있으며 누출이 아니다.

정책 Boolean은 연구계획의 넓은 거리두기 대응 캠페인을 의미한다. 생물학적 감염병 발발/종식의 대용이나 개별 단계 강도는 아니다. 2022-04-15 발표 전 원점에는 향후 해제를 미리 알려주지 않고 당시의 활성 상태를 유지한다. 발표 후에는 4월18일 시행부터 False를 입력한다. 2023–2024년 False 평가구간의 성적으로 다음 감염병 대응력을 입증했다고 표현하지 않는다.

## 실행·보존

기존 [XGBoost/Ridge 방법론](XGBOOST.md)의 Expanding·월 첫 월요일 재적합·주별 H=14·pooled 직접 예측을 유지한다. 주 모형은 환자 구성과 달력/정부 대응기간을 모두 포함하며 k_y/k_p=7/28을 독립 개발 선택한다. 같은 원점·목표로 XGBoost와 Ridge를 비교한다. 정부 대응기간을 누락한 이전 결과로 이 실행을 대체하지 않는다. 2024년은 이미 열람한 후향적 비교라는 한계를 유지한다.

- [고정 설정](../../configs/xgboost_standard_features.json)
- [표준 환자 집계·KCD·연령 코드](../../src/methods/xgboost/patient_data.py)
- [공휴일·정부 정책 기준과 원점 가용성 코드](../../src/methods/xgboost/known_calendar.py)
- [예측 코드](../../src/methods/xgboost/forecasting.py)
- [발표 이후 정보·KCD/나이 경계 검사](../../tests/test_calendar_and_kcd.py)
- [수정 실행 결과](../../runs/cheonan/xgboost_standard_features_20261007_01/report.md)

원자료와 기존 실행을 덮어쓰지 않는다. 이전 실행 코드는 해당 run의 source_snapshot에 보존했다. 표준 환자 집계는 patient_standard_20261007_02, 외부 달력/정부 기준은 data/processed/external/korean_calendar/calendar_20261007_01에 저장했다. 외부 발표 테이블을 바꾸어도 발표 이전 입력이 변하지 않는 검사를 수행한다.

## 18개월 시계열 사후 점검

선정된 모형의 2023년 하반기 검증과 2024년 후향적 평가 예측을 연결했다. 매주 앞 7일의 중복 없는 일별 그래프와 원점 월별 앞 14일 MAE를 구분하여 저장한다. 새 적합·튜닝은 수행하지 않는다. [장기 점검 보고서](../../runs/cheonan/xgboost_standard_features_20261007_01/longitudinal_report.md), [방법론 보존 코드](../../src/methods/xgboost/longitudinal.py).

최신 본문은 품목 이름 → 변수 → 모델 → 피팅·예측 → 그림 → 수치 순서로 작성한다. `src.methods.xgboost.longitudinal` 실행으로 `report.md`와 `longitudinal_report.md`를 동일하게 재생성하며, 이전 상세 감사 보고서는 `technical_appendix.md`에 보존한다.
