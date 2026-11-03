# 6단계 API와 DB 명세 작성 기준

설계 버전 0.2 · 2026-10-21 · 상세 명세 기준 · 현재 구현·검증은 8단계 참조

테이블 소유권과 API의 처리 계약을 정한다. 상세 명세와 구조 SQL은 아래 문서에 구체화했다. DB·API의 실행 서비스 구현과 실제 업무 검증은 별도다.

[설계 진입점](README.md) · [현재 구현과 검증](08_IMPLEMENTATION.md)

## 상세 명세

- [엔드포인트별 API 기능 명세](06_contracts/API_SPEC.md)
- [OpenAPI 요청과 응답 계약](06_contracts/openapi.json)
- [DB 테이블과 관계도](06_contracts/DATABASE_SCHEMA.md)
- [PostgreSQL 구조 SQL](06_contracts/schema.sql)
- [업무 추적표와 트랜잭션](06_contracts/BUSINESS_COVERAGE.md)
- [독립 검토와 반영 기록](06_contracts/INDEPENDENT_REVIEW.md)
- [명세 파일 해시와 구조 검증 결과](06_contracts/validation_results.json)

현재 상세 명세는 API 동작35개·테이블46개·업무 추적항목35개를 포함한다. OpenAPI·SQL 구조 검증과 독립 검토의 범위·한계는 검토 기록을 따른다. 실행 서비스가 업무 로직을 수행한다는 의미는 아니다.

## DB 책임과 향후 테이블 경계

다음은 논리 소유권 요약이다. 컬럼·인덱스·외래키·RLS·입력 불변성 보호는 상세 DB 명세와 구조 SQL을 따른다. 배포 migration과 역할별 GRANT는 후속 구현이다.

| 영역 | 주요 데이터 | 쓰기 소유자 |
|---|---|---|
| 접수·정정 | 제출, 파일 참조, revision, 검증 실패 | API 접수 / 수신 검증의 상태별 분리 |
| 예측 입력 | 일별 목표·환자 집계·달력 참조, 봉인 snapshot | 수신·검증 |
| 모델·예측 | 모델 manifest, 실행·시도, 품목·날짜별 예측 | 모델 등록 관리 / TimesFM |
| 재고·정책 | 기준 스냅샷·재고 사건·정책 버전 | 수신·검증 / 업무 |
| 의사결정 | 주문안·승인 이력·발주 예약 | 업무가 주문안, API가 승인 |
| 주문·입고 | 주문 의도·전송 시도·접수 확인·입고 사건 | API가 승인과 의도 생성, 업무가 전송·입고 |
| 실행 전달 | 작업·시도·선점 토큰·Outbox·처리 이력 | 담당 실행기와 제한된 전달·복구 역할 |
| 기술 로그 | 구조화 로그 | 로거 전용 |

모듈별 별도 DB 계정을 사용한다. API의 승인 변경, ML의 입력 조회·예측 저장, 로거의 로그 쓰기, 보존 작업의 로그 삭제 권한을 분리한다. 어떤 애플리케이션도 DB superuser로 실행하지 않는다. 공통 migration은 배포 시 한 주체가 수행하며 각 실행기가 임의로 스키마를 바꾸지 않는다.

현재 DB 상태 외에 승인·주문·재고 조정 이력을 보존한다. 재전달·재시도에서 고유 제약과 상태 조건을 DB에 함께 적용한다. 입력·실행·업무 이력은 연구 시나리오 보존기간 동안 유지하며 기술 로그의 30일 삭제 대상에서 제외한다.

## API 기능 명세로 전달할 계약

아래는 핵심 API 경계의 요약이다. 전체 엔드포인트·요청 필드·응답·오류·페이지네이션·권한은 상세 API 명세와 OpenAPI를 따른다. 기본 경로는 `/api/v1`이다.

| API | 책임과 확정 응답 |
|---|---|
| `POST /weekly-deliveries` | 원본 확정·접수·작업·Outbox 커밋 후 202와 `delivery_id / job_id / status_url` |
| `GET /weekly-deliveries/{id}` | 제출·정정 관계·완료 범위·검증 실패 |
| `GET /jobs/{id}` | DB 기준 단계·시도·대기·실패·결과 참조, 실패를 과거 성공으로 대체하지 않음 |
| `POST /jobs/{id}/retry` | 허용된 실패 작업만 새 시도 예약, 입력 변경 시 새 제출 요구 |
| `GET /forecasts/{id}/values` | 원점·생성시각·품목·단위·날짜·모델·입력 버전 |
| `GET /order-proposals/{id}` | 권고량·포장·산출 근거·버전·해시·유효기한·차단 사유 |
| `POST /order-proposals/{id}/recalculate` | 기존 주문·예약·결과 불명 상태를 반영한 새 버전 생성 |
| `POST /order-proposals/{id}/decisions` | 인증된 승인자, 버전·해시·Yes/No 검사 후 승인·의도·Outbox 원자적 확정 |
| `GET /purchase-orders/{id}` | 승인과 접수 구분, 전송 결과 불명·부분 입고·잔량 표시 |
| `POST /purchase-orders/{id}/receipts` | 입고 접수 작업 생성, `receipt.apply`에서 영속 사건 키로 중복 방지 |
| `POST /test/scenarios` | 식별 가능한 가상 초기 이력·재고·정책 준비, 일반 데이터 영역과 격리 |
| `POST /test/scenarios/{id}/weekly-deliveries` | 다음 가상 주간 자료를 일반 제출 경로에 전달, 승인 생략 없음 |
| `GET /health/live`, `GET /health/ready` | 프로세스 생존과 의존성·이식 검증·모델 준비 상태를 구분 |

변경 요청에는 인증 주체·경로·Idempotency-Key·본문 해시를 연결한다. 같은 키·같은 내용은 기존 응답을 반환하고 다른 내용은 409를 반환한다. 키 재사용 방지는 관련 업무 보존기간 동안 유지한다. 형식 오류·검증 반려·상태 충돌·의존성 불가는 서로 다른 오류 코드로 표시한다. 테스트 API는 테스트 프로필에서만 활성화한다.
