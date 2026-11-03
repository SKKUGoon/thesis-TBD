# API 기능 명세

명세 버전 1.2.0 · 2026-10-22 · Linux 가상 운영 서버와 프론트엔드 연결 구현

[설계 진입점](../README.md) · [OpenAPI 3.1 계약](openapi.json) · [DB 구조](DATABASE_SCHEMA.md) · [업무 추적표](BUSINESS_COVERAGE.md)

## 공통 요청과 응답

- 기본 경로는 /api/v1이다. Bearer 인증과 X-Workspace-ID로 범위를 확인한다. 다른 범위의 ID는 404, 같은 범위의 역할 부족은 403이다. 신규 시나리오 생성과 본인 생성 목록 조회는 플랫폼 test_admin이 scope 헤더 없이 호출한다. `/me`는 인증만으로 주체를 확인하며 선택적으로 scope 헤더를 받아 현재 membership을 반환한다.
- 모든 POST에는 Idempotency-Key가 필요하다. 주체·경로·범위·키와 정규화 본문 해시를 묶어 저장하고 동일 요청은 기존 응답을 반환한다. 키가 같고 본문이 다르면 409다.
- 날짜는 ISO 8601, 업무 시간대는 Asia/Seoul이다. 실제 수신/생성 시각과 가상 업무시각을 구분한다. 수량은 소수 문자열이며 단위를 함께 제공한다. 환자 집계는 비음수 정수다.
- 목록은 limit=1~100과 불투명 cursor를 사용하며 items·next_cursor를 반환한다. created_at과 id의 안정된 순서로 읽고, 예측값은 대상일·품목·id 순서다. cursor에는 범위·필터·정렬을 묶고 변조·다른 필터 재사용은 400이다.
- 202는 접수 커밋만 의미한다. Location·Retry-After와 job_id를 반환한다. GET /jobs/{id}의 단계 성공 및 자식 작업으로 다음 처리 상태를 확인한다.
- 역할 목록의 쉼표는 그중 하나의 역할을 뜻한다. 빈 목록은 200과 items=[]다. 승인 차단은 409, 자료의 형식 오류는 422다. 비동기 의미 검증 실패는 제출·작업 조회에서 확인한다.
- OpenAPI는 요청·응답 스키마·성공 예시·공통 오류를 포함한다. 에러 코드의 세부 업무 조건과 트랜잭션은 업무 추적표가 기준이다. [OpenAPI 공식 규격](https://spec.openapis.org/oas/v3.1.0)

## 권한

| 역할 | 허용 기능 |
|---|---|
| viewer | 같은 scope의 품목·정책·재고·제출·예측·주문안·주문·입고·작업 조회 |
| uploader | 주간 제출, viewer 권한은 별도 membership |
| approver | Yes/No 결정 |
| operator | 작업 목록·재시도·재계산·접수 확인 |
| admin | 새 주문 정책 등록 |
| receiver | 입고·역분개 접수 |
| test_operator | 기존 가상 시나리오 조회·다음 주간 전달 |
| test_admin | 플랫폼 권한으로 새 시나리오 생성·readiness;  호출자에게 생성 scope의 필요한 역할 부여 |
| log_reader | 비식별 기술 로그 조회 |

## 엔드포인트 목록

| Method | 경로 | 기능 | 성공 | 역할 |
|---|---|---|---|---|
| GET | `/me` | 인증 주체·현재 범위 역할 | 200 | authenticated |
| GET | `/test/scenarios` | 본인이 생성한 접근 가능 시나리오 목록 | 200 | test_admin |
| GET | `/items` | 품목 목록 | 200 | viewer |
| GET | `/items/{item_id}` | 품목 상세 | 200 | viewer |
| GET | `/items/{item_id}/ordering-policies` | 정책 버전 이력 | 200 | viewer |
| POST | `/items/{item_id}/ordering-policies` | 새 주문 정책 버전 등록 | 201 | admin |
| GET | `/inventory` | 현재 재고와 미입고 조회 | 200 | viewer |
| POST | `/weekly-deliveries` | 주간 전체 자료 제출 | 202 | uploader |
| GET | `/weekly-deliveries` | 제출 이력 조회 | 200 | viewer |
| GET | `/weekly-deliveries/{delivery_id}` | 제출 상세 | 200 | viewer |
| GET | `/weekly-deliveries/{delivery_id}/issues` | 제출 검증 결과 | 200 | viewer |
| GET | `/jobs` | 작업 이력 | 200 | operator |
| GET | `/jobs/{job_id}` | 단계별 작업 상태 | 200 | viewer |
| POST | `/jobs/{job_id}/retry` | 실패 작업 재시도 예약 | 202 | operator |
| GET | `/forecasts` | 예측 실행 목록 | 200 | viewer |
| GET | `/forecasts/{forecast_id}` | 예측 실행 상세 | 200 | viewer |
| GET | `/forecasts/{forecast_id}/values` | 품목별 14일 예측 | 200 | viewer |
| GET | `/order-proposals` | 주문안 목록 | 200 | viewer |
| GET | `/order-proposals/{proposal_id}` | 승인 대상 주문안 상세 | 200 | viewer |
| POST | `/order-proposals/{proposal_id}/recalculate` | 최신 상태로 새 주문안 계산 | 202 | operator |
| POST | `/order-proposals/{proposal_id}/decisions` | Yes 또는 No 확정 | 200 | approver |
| GET | `/purchase-orders` | 주문 목록 | 200 | viewer |
| GET | `/purchase-orders/{order_id}` | 접수·미입고·부분입고 조회 | 200 | viewer |
| POST | `/purchase-orders/{order_id}/reconcile` | 불명 주문 접수 조회 | 202 | operator |
| POST | `/purchase-orders/{order_id}/receipts` | 부분 또는 전체 입고 접수 | 202 | receiver |
| GET | `/receipts/{receipt_id}` | 입고 적용 상태 조회 | 200 | viewer |
| POST | `/receipts/{receipt_id}/reversals` | 잘못 반영한 입고 전체 역분개 | 202 | receiver |
| POST | `/test/scenarios` | 가상 초기 이력과 시나리오 생성 | 202 | test_admin |
| GET | `/test/scenarios/{scenario_id}` | 가상 시나리오 진행 상태 | 200 | test_operator |
| POST | `/test/scenarios/{scenario_id}/weekly-deliveries` | 다음 한 주 진행과 마감 자료 전달 | 202 | test_operator |
| GET | `/logs` | 권한 있는 기술 로그 조회 | 200 | log_reader |
| GET | `/health/live` | API 프로세스 생존 | 200 | public |
| GET | `/health/ready` | API 접수 가능 상태 | 200 | test_admin |
| GET | `/model-versions/{model_version_id}/input-contract` | 고정 모델 입력 명단·특징 정의 조회 | 200 | viewer, uploader |
| GET | `/forecasts/{forecast_id}/evaluations` | 첫째·둘째 주 정확도 조회 | 200 | viewer |
| POST | `/purchase-orders/{order_id}/release` | 미전송 또는 최종 거절 주문의 예약 해제 | 200 | operator |
| GET | `/test/scenarios/{scenario_id}/demand-outcomes` | 처리된 가상 수요·충족·부족 조회 | 200 | test_operator |

## 프론트엔드 연결용 추가 조회

### GET /me

Bearer 인증 필수. `X-Workspace-ID`는 선택입니다. scope가 있으면 UUID와 실시간 membership을 확인하며, 접근 불가한 범위는 404입니다. 토큰을 브라우저에서 해석한 역할로 권한을 확정하지 않습니다.

```json
{"id":"00000000-0000-4000-8000-000000000015","platform_role":"test_admin","workspace_id":null,"roles":[]}
```

범위를 지정하면 `workspace_id`와 해당 범위의 `roles`를 반환합니다. 서명 비밀값·JWT 원문·DB 자격정보를 반환하지 않습니다.

### GET /test/scenarios

플랫폼 `test_admin`만 호출합니다. `limit=1~100`, `cursor`를 받으며 scope 헤더는 필요하지 않습니다. **호출자가 직접 생성한 범위 중 현재 `test_operator` 또는 `admin` membership이 남은 범위만** 반환합니다. 공유받은 범위의 자동 탐색은 지원하지 않으며 권한이 있는 ID로 별도 열 수 있습니다. 기존 `scenario_creation_request`를 후보로 읽고 각 scope의 RLS 트랜잭션에서 membership을 재확인하므로 전체 scope 열람 권한을 API 계정에 추가하지 않습니다.

```json
{"items":[],"next_cursor":null}
```

배열 항목은 기존 `Scenario`와 같으며 실제 가상 시각·버전·다음 주간·처리 상태를 포함합니다. cursor는 주체·경로·필터에 바인딩하여 서명하며 다른 주체 재사용·변조는 400입니다. 새 테이블이나 DB migration은 필요하지 않습니다.

## 엔드포인트별 요청과 응답

### GET /items

품목 목록

권한: viewer. 연관 테이블: `item`, `ordering_policy`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `kind` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `ItemPage`.

```json
{
  "items": [
    {
      "id": "10000000-0000-4000-8000-000000000001",
      "code": "SYN-001",
      "name": "가상 약품 A",
      "kind": "drug",
      "base_unit": "개",
      "target_semantics": "drug_record_units",
      "active": true,
      "current_policy_id": "00000000-0000-4000-8000-000000000004"
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /items/{item_id}

품목 상세

권한: viewer. 연관 테이블: `item`, `ordering_policy`.

요청 경로·쿼리: path `item_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Item`.

```json
{
  "id": "10000000-0000-4000-8000-000000000001",
  "code": "SYN-001",
  "name": "가상 약품 A",
  "kind": "drug",
  "base_unit": "개",
  "target_semantics": "drug_record_units",
  "active": true,
  "current_policy_id": "00000000-0000-4000-8000-000000000004"
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /items/{item_id}/ordering-policies

정책 버전 이력

권한: viewer. 연관 테이블: `ordering_policy`.

요청 경로·쿼리: path `item_id` (필수); query `limit` (선택); query `cursor` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `OrderingPolicyPage`.

```json
{
  "items": [
    {
      "supplier_id": "00000000-0000-4000-8000-000000000003",
      "lead_days": 2,
      "review_days": 7,
      "pack_size": "10",
      "minimum_packs": 1,
      "safety_units": "20",
      "valid_from": "2026-10-26T00:00:00+09:00",
      "calendar_rule": {
        "mode": "calendar_days",
        "business_weekdays": [
          1,
          2,
          3,
          4,
          5,
          6,
          7
        ],
        "cutoff_local": "23:59",
        "timezone": "Asia/Seoul",
        "arrival_local": "00:00"
      },
      "id": "00000000-0000-4000-8000-000000000004",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "version": 1,
      "policy_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /items/{item_id}/ordering-policies

품목 잠금 후 expected_version 검사. 정책 불변 행과 이력 저장. 기존 승인안은 변경하지 않고 미승인 주문안을 무효화한다. 모델 명단은 변경하지 않는다.

권한: admin. 연관 테이블: `ordering_policy`, `proposal`, `business_event`.

요청 경로·쿼리: path `item_id` (필수).

요청 본문: `OrderingPolicyRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "expected_version": 0,
  "supplier_id": "00000000-0000-4000-8000-000000000003",
  "lead_days": 2,
  "review_days": 7,
  "pack_size": "10",
  "minimum_packs": 1,
  "safety_units": "20",
  "valid_from": "2026-10-26T00:00:00+09:00",
  "calendar_rule": {
    "mode": "calendar_days",
    "business_weekdays": [
      1,
      2,
      3,
      4,
      5,
      6,
      7
    ],
    "cutoff_local": "23:59",
    "timezone": "Asia/Seoul",
    "arrival_local": "00:00"
  }
}
```

성공 응답: HTTP 201, `OrderingPolicy`.

```json
{
  "supplier_id": "00000000-0000-4000-8000-000000000003",
  "lead_days": 2,
  "review_days": 7,
  "pack_size": "10",
  "minimum_packs": 1,
  "safety_units": "20",
  "valid_from": "2026-10-26T00:00:00+09:00",
  "calendar_rule": {
    "mode": "calendar_days",
    "business_weekdays": [
      1,
      2,
      3,
      4,
      5,
      6,
      7
    ],
    "cutoff_local": "23:59",
    "timezone": "Asia/Seoul",
    "arrival_local": "00:00"
  },
  "id": "00000000-0000-4000-8000-000000000004",
  "item_id": "10000000-0000-4000-8000-000000000001",
  "version": 1,
  "policy_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

오류 상태: 201, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: POLICY_VERSION_CONFLICT, PACK_UNIT_INVALID.

### GET /inventory

현재 재고와 미입고 조회

권한: viewer. 연관 테이블: `inventory_state`, `purchase_order`, `purchase_order_line`, `order_reservation`, `external_open_order`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `item_id` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `InventoryPage`.

```json
{
  "items": [
    {
      "item_id": "10000000-0000-4000-8000-000000000001",
      "unit": "개",
      "as_of": "2026-10-26T00:00:00+09:00",
      "version": 1,
      "on_hand": "40",
      "unavailable": "0",
      "allocated": "0",
      "available_units": "40",
      "confirmed_incoming_units": "20",
      "pending_order_units": "0",
      "unknown_order_ids": [],
      "blocking_codes": []
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /weekly-deliveries

최대 2 MiB JSON. 원본 파일 확정 후 delivery·job·outbox·idempotency를 원자적으로 저장한다. 행 의미 검사는 비동기이며 불합격은 delivery.rejected와 job.blocked로 조회한다. 정정은 같은 주간의 직전 제출과 새 revision을 지정한다. 과거 정정의 영향 원점은 delivery.correction_impacts에서 확인하고 해당 최신 주간을 전체 새 revision으로 재제출한다.

권한: uploader. 연관 테이블: `artifact`, `delivery`, `job`, `outbox`, `api_idempotency`.

요청 경로·쿼리: 없음.

요청 본문: `WeeklyDeliveryRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

[완전한 30품목·7일·69개 환자 채널 합성 요청 예시](examples/weekly_delivery.json). 품목·모델 ID를 해당 시나리오 등록값에 맞춰야 한다. 이 예시는 형식 확인용이며 실제 병원 자료가 아니다.

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000006",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 413, 422, 500, 503 (첫 값은 성공). 업무 오류: WEEK_RANGE_INVALID, REVISION_CONFLICT, MODEL_NOT_VALIDATED, BODY_TOO_LARGE.

### GET /weekly-deliveries

제출 이력 조회

권한: viewer. 연관 테이블: `delivery`, `job`, `input_snapshot`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `week_start` (선택); query `status` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `DeliveryPage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000006",
      "kind": "weekly",
      "week_start": "2026-10-19",
      "week_end_exclusive": "2026-10-26",
      "source_revision": 1,
      "supersedes_delivery_id": null,
      "source_closed_at": "2026-10-26T00:00:00+09:00",
      "source_available_at": "2026-10-26T00:00:00+09:00",
      "received_at": "2026-10-21T03:00:00Z",
      "status": "validated",
      "job_id": "00000000-0000-4000-8000-000000000007",
      "snapshot_id": "00000000-0000-4000-8000-000000000008",
      "artifact_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "issue_count": 0,
      "correction_impacts": []
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /weekly-deliveries/{delivery_id}

제출 상세

권한: viewer. 연관 테이블: `delivery`, `job`, `input_snapshot`, `validation_issue`.

요청 경로·쿼리: path `delivery_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Delivery`.

```json
{
  "id": "00000000-0000-4000-8000-000000000006",
  "kind": "weekly",
  "week_start": "2026-10-19",
  "week_end_exclusive": "2026-10-26",
  "source_revision": 1,
  "supersedes_delivery_id": null,
  "source_closed_at": "2026-10-26T00:00:00+09:00",
  "source_available_at": "2026-10-26T00:00:00+09:00",
  "received_at": "2026-10-21T03:00:00Z",
  "status": "validated",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "snapshot_id": "00000000-0000-4000-8000-000000000008",
  "artifact_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "issue_count": 0,
  "correction_impacts": []
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /weekly-deliveries/{delivery_id}/issues

제출 검증 결과

권한: viewer. 연관 테이블: `validation_issue`.

요청 경로·쿼리: path `delivery_id` (필수); query `limit` (선택); query `cursor` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `ValidationIssuePage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000016",
      "code": "UNIT_MISMATCH",
      "field_path": "usage[0].unit",
      "severity": "error",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "message": "기본 단위가 일치하지 않습니다."
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /jobs

작업 이력

권한: operator. 연관 테이블: `job`, `job_attempt`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `stage` (선택); query `status` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `JobPage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000007",
      "parent_job_id": null,
      "stage": "forecast",
      "status": "succeeded",
      "current_attempt": 1,
      "heartbeat_at": "2026-10-21T03:00:10Z",
      "retry_at": null,
      "error_code": null,
      "retryable": false,
      "result": {
        "delivery_id": "00000000-0000-4000-8000-000000000006",
        "snapshot_id": "00000000-0000-4000-8000-000000000008",
        "forecast_id": "00000000-0000-4000-8000-000000000009",
        "proposal_id": null,
        "order_ids": [],
        "receipt_id": null,
        "child_job_ids": [],
        "required_resubmission_delivery_ids": []
      },
      "updated_at": "2026-10-21T03:00:20Z"
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /jobs/{job_id}

해당 단계 성공과 전체 workflow 완료를 구분한다. result.child_job_ids로 다음 단계 조회. 미래 단계 결과를 추측하지 않는다.

권한: viewer. 연관 테이블: `job`, `job_attempt`.

요청 경로·쿼리: path `job_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Job`.

```json
{
  "id": "00000000-0000-4000-8000-000000000007",
  "parent_job_id": null,
  "stage": "forecast",
  "status": "succeeded",
  "current_attempt": 1,
  "heartbeat_at": "2026-10-21T03:00:10Z",
  "retry_at": null,
  "error_code": null,
  "retryable": false,
  "result": {
    "delivery_id": "00000000-0000-4000-8000-000000000006",
    "snapshot_id": "00000000-0000-4000-8000-000000000008",
    "forecast_id": "00000000-0000-4000-8000-000000000009",
    "proposal_id": null,
    "order_ids": [],
    "receipt_id": null,
    "child_job_ids": [],
    "required_resubmission_delivery_ids": []
  },
  "updated_at": "2026-10-21T03:00:20Z"
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /jobs/{job_id}/retry

일시 오류이고 최대 4회 내인 failed/blocked 작업만 허용. 이미 running/queued이면 409. 새 입력 필요 오류는 정정 제출. submit 타임아웃은 재전송하지 않고 reconcile로 전환.

권한: operator. 연관 테이블: `job`, `job_attempt`, `outbox`, `business_event`.

요청 경로·쿼리: path `job_id` (필수).

요청 본문: `RetryRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "expected_attempt": 1,
  "reason": "일시적 장치 오류 해소"
}
```

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000007",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: NOT_RETRYABLE, ATTEMPTS_EXHAUSTED, ATTEMPT_CONFLICT.

### GET /forecasts

예측 실행 목록

권한: viewer. 연관 테이블: `forecast_run`, `input_snapshot`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `forecast_origin` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `ForecastPage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000009",
      "snapshot_id": "00000000-0000-4000-8000-000000000008",
      "model_version_id": "00000000-0000-4000-8000-000000000005",
      "forecast_origin": "2026-10-26",
      "input_cutoff_at": "2026-10-26T00:00:00+09:00",
      "knowledge_as_of": "2026-10-26T00:00:00+09:00",
      "generated_at": "2026-10-21T03:00:20Z",
      "horizon_days": 14,
      "status": "succeeded",
      "validity": "current",
      "job_id": "00000000-0000-4000-8000-000000000007",
      "expected_values": 420,
      "stored_values": 420
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /forecasts/{forecast_id}

예측 실행 상세

권한: viewer. 연관 테이블: `forecast_run`, `input_snapshot`, `model_version`.

요청 경로·쿼리: path `forecast_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Forecast`.

```json
{
  "id": "00000000-0000-4000-8000-000000000009",
  "snapshot_id": "00000000-0000-4000-8000-000000000008",
  "model_version_id": "00000000-0000-4000-8000-000000000005",
  "forecast_origin": "2026-10-26",
  "input_cutoff_at": "2026-10-26T00:00:00+09:00",
  "knowledge_as_of": "2026-10-26T00:00:00+09:00",
  "generated_at": "2026-10-21T03:00:20Z",
  "horizon_days": 14,
  "status": "succeeded",
  "validity": "current",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "expected_values": 420,
  "stored_values": 420
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /forecasts/{forecast_id}/values

성공 실행만 값 조회. date_to는 포함일. 분위수 순서는 0.1부터 0.9이며 prediction=max(0,q50). 생성 당시 입력·원점을 유지한다.

권한: viewer. 연관 테이블: `forecast_value`, `item`.

요청 경로·쿼리: path `forecast_id` (필수); query `limit` (선택); query `cursor` (선택); query `item_id` (선택); query `date_from` (선택); query `date_to` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `ForecastValuePage`.

```json
{
  "items": [
    {
      "item_id": "10000000-0000-4000-8000-000000000001",
      "item_name": "가상 약품 A",
      "unit": "개",
      "target_day": "2026-10-26",
      "horizon_day": 1,
      "prediction_raw": "10",
      "prediction": "10",
      "quantiles": [
        "5",
        "6",
        "7",
        "8",
        "10",
        "12",
        "13",
        "14",
        "15"
      ]
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: FORECAST_NOT_READY.

### GET /order-proposals

주문안 목록

권한: viewer. 연관 테이블: `proposal`, `proposal_line`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `status` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `ProposalPage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000010",
      "forecast_id": "00000000-0000-4000-8000-000000000009",
      "version": 1,
      "supersedes_id": null,
      "status": "pending",
      "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "decision_as_of": "2026-10-26T00:00:00+09:00",
      "expires_at": "2026-10-26T09:00:00+09:00",
      "next_demand_at": "2026-10-26T09:00:00+09:00",
      "blocking_codes": [],
      "lines": [
        {
          "id": "00000000-0000-4000-8000-000000000017",
          "item_id": "10000000-0000-4000-8000-000000000001",
          "item_name": "가상 약품 A",
          "unit": "개",
          "policy_id": "00000000-0000-4000-8000-000000000004",
          "inventory_version": 1,
          "protection_end": "2026-11-04",
          "forecast_units": "90",
          "safety_units": "20",
          "available_units": "40",
          "eligible_incoming": "20",
          "reserved_order_units": "0",
          "required_units": "50",
          "packs": 5,
          "order_units": "50",
          "blocking_codes": []
        }
      ]
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /order-proposals/{proposal_id}

승인 대상 주문안 상세

권한: viewer. 연관 테이블: `proposal`, `proposal_line`, `ordering_policy`.

요청 경로·쿼리: path `proposal_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Proposal`.

```json
{
  "id": "00000000-0000-4000-8000-000000000010",
  "forecast_id": "00000000-0000-4000-8000-000000000009",
  "version": 1,
  "supersedes_id": null,
  "status": "pending",
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "decision_as_of": "2026-10-26T00:00:00+09:00",
  "expires_at": "2026-10-26T09:00:00+09:00",
  "next_demand_at": "2026-10-26T09:00:00+09:00",
  "blocking_codes": [],
  "lines": [
    {
      "id": "00000000-0000-4000-8000-000000000017",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "item_name": "가상 약품 A",
      "unit": "개",
      "policy_id": "00000000-0000-4000-8000-000000000004",
      "inventory_version": 1,
      "protection_end": "2026-11-04",
      "forecast_units": "90",
      "safety_units": "20",
      "available_units": "40",
      "eligible_incoming": "20",
      "reserved_order_units": "0",
      "required_units": "50",
      "packs": 5,
      "order_units": "50",
      "blocking_codes": []
    }
  ]
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /order-proposals/{proposal_id}/recalculate

기존 내용을 수정하지 않고 새 버전 계산 작업 생성. 동일 forecast·같은 기준의 중복 작업은 기존 작업 반환. 승인된 주문은 취소하지 않고 발주 예약·미입고를 포함한다.

권한: operator. 연관 테이블: `proposal`, `job`, `outbox`, `purchase_order`, `order_reservation`.

요청 경로·쿼리: path `proposal_id` (필수).

요청 본문: `RecalculateRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "expected_version": 1,
  "reason": "입고 반영 후 재계산"
}
```

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000010",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: PROPOSAL_VERSION_CONFLICT, FORECAST_OBSOLETE, ORDER_UNRESOLVED.

### POST /order-proposals/{proposal_id}/decisions

scope·품목 잠금 순서 고정 후 내용·재고·정책·정정·만료 검사. approve는 결정·공급처별 양의 주문·예약·submit job·outbox를 원자적으로 생성. reject는 결정·이력만 저장. 전 품목 0이면 승인 이력만 저장하고 order_ids=[] 및 not_required 반환.

권한: approver. 연관 테이블: `proposal`, `decision`, `purchase_order`, `purchase_order_line`, `order_reservation`, `job`, `outbox`, `business_event`.

요청 경로·쿼리: path `proposal_id` (필수).

요청 본문: `DecisionRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "proposal_version": 1,
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "decision": "approve"
}
```

성공 응답: HTTP 200, `Decision`.

```json
{
  "id": "00000000-0000-4000-8000-000000000011",
  "proposal_id": "00000000-0000-4000-8000-000000000010",
  "proposal_version": 1,
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "decision": "approve",
  "decided_by": "00000000-0000-4000-8000-000000000015",
  "decided_at": "2026-10-26T00:00:00+09:00",
  "order_ids": [
    "00000000-0000-4000-8000-000000000012"
  ],
  "dispatch_status": "queued"
}
```

오류 상태: 200, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: PROPOSAL_STALE, PROPOSAL_EXPIRED, PROPOSAL_BLOCKED, ALREADY_DECIDED, ORDER_UNRESOLVED.

### GET /purchase-orders

주문 목록

권한: viewer. 연관 테이블: `purchase_order`, `purchase_order_line`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `status` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `PurchaseOrderPage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000012",
      "decision_id": "00000000-0000-4000-8000-000000000011",
      "supplier_id": "00000000-0000-4000-8000-000000000003",
      "order_key": "00000000-0000-4000-8000-000000000012",
      "status": "accepted",
      "receipt_status": "partially_received",
      "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "released_at": null,
      "supplier_order_ref": "SYN-PO-001",
      "accepted_at": "2026-10-26T00:00:00+09:00",
      "lines": [
        {
          "id": "00000000-0000-4000-8000-000000000013",
          "proposal_line_id": "00000000-0000-4000-8000-000000000017",
          "item_id": "10000000-0000-4000-8000-000000000001",
          "unit": "개",
          "packs": 5,
          "ordered_units": "50",
          "received_units": "20",
          "remaining_units": "30",
          "expected_at": "2026-10-28T00:00:00+09:00"
        }
      ]
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /purchase-orders/{order_id}

접수·미입고·부분입고 조회

권한: viewer. 연관 테이블: `purchase_order`, `purchase_order_line`, `order_attempt`.

요청 경로·쿼리: path `order_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `PurchaseOrder`.

```json
{
  "id": "00000000-0000-4000-8000-000000000012",
  "decision_id": "00000000-0000-4000-8000-000000000011",
  "supplier_id": "00000000-0000-4000-8000-000000000003",
  "order_key": "00000000-0000-4000-8000-000000000012",
  "status": "accepted",
  "receipt_status": "partially_received",
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "released_at": null,
  "supplier_order_ref": "SYN-PO-001",
  "accepted_at": "2026-10-26T00:00:00+09:00",
  "lines": [
    {
      "id": "00000000-0000-4000-8000-000000000013",
      "proposal_line_id": "00000000-0000-4000-8000-000000000017",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "unit": "개",
      "packs": 5,
      "ordered_units": "50",
      "received_units": "20",
      "remaining_units": "30",
      "expected_at": "2026-10-28T00:00:00+09:00"
    }
  ]
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /purchase-orders/{order_id}/reconcile

sending lease 만료 또는 submission_unknown만 키 조회. 조회 실패를 미접수로 간주하지 않는다. 확정 not_found이면 동일 키·동일 내용으로만 재전송 가능.

권한: operator. 연관 테이블: `purchase_order`, `order_attempt`, `job`, `outbox`.

요청 경로·쿼리: path `order_id` (필수).

요청 본문: `ReconcileRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "reason": "공급처 응답 유실 확인"
}
```

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000012",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: ORDER_NOT_UNCERTAIN.

### POST /purchase-orders/{order_id}/receipts

accepted 주문만 접수. 같은 source_receipt_key·본문은 기존 receipt/job 반환. 다른 내용은 409. job 완료 전 재고 증가 아님. 가상 현재시각보다 미래 사건은 422. 현재 anchor 이전 사건은 주문잔량만 갱신하고 현재 재고에는 다시 가산하지 않음.

권한: receiver. 연관 테이블: `receipt`, `receipt_line`, `job`, `outbox`.

요청 경로·쿼리: path `order_id` (필수).

요청 본문: `ReceiptRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "source_receipt_key": "SYN-RCV-001",
  "occurred_at": "2026-10-28T00:00:00+09:00",
  "lines": [
    {
      "order_line_id": "00000000-0000-4000-8000-000000000013",
      "quantity": "20",
      "unit": "개"
    }
  ]
}
```

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000014",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: RECEIPT_KEY_CONFLICT, ORDER_ACCEPTANCE_UNCONFIRMED, OVER_RECEIPT, UNIT_MISMATCH, FUTURE_RECEIPT.

### GET /receipts/{receipt_id}

입고 적용 상태 조회

권한: viewer. 연관 테이블: `receipt`, `receipt_line`, `job`.

요청 경로·쿼리: path `receipt_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Receipt`.

```json
{
  "source_receipt_key": "SYN-RCV-001",
  "occurred_at": "2026-10-28T00:00:00+09:00",
  "lines": [
    {
      "order_line_id": "00000000-0000-4000-8000-000000000013",
      "quantity": "20",
      "unit": "개"
    }
  ],
  "id": "00000000-0000-4000-8000-000000000014",
  "order_id": "00000000-0000-4000-8000-000000000012",
  "status": "applied",
  "reverses_receipt_id": null,
  "job_id": "00000000-0000-4000-8000-000000000007"
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /receipts/{receipt_id}/reversals

부분 역분개는 초기 미지원. 원 입고 라인 전체의 반대 사건을 생성한다. 적용 후 수정된 입고는 새 키로 접수. 원 입고·주문은 삭제하지 않는다. applied 일반 입고만 대상이며 역분개를 다시 역분개할 수 없다. 원 입고시각<=역분개시각<=가상 현재시각을 검사한다. 원 입고가 현재 anchor 이전이면 초기 자동 역분개는 차단한다.

권한: receiver. 연관 테이블: `receipt`, `receipt_line`, `inventory_event`, `inventory_state`, `purchase_order_line`, `job`, `outbox`.

요청 경로·쿼리: path `receipt_id` (필수).

요청 본문: `ReceiptReversalRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "source_receipt_key": "SYN-REV-001",
  "occurred_at": "2026-10-28T01:00:00+09:00",
  "reason": "입고 통지 정정"
}
```

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000014",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: RECEIPT_NOT_APPLIED, ALREADY_REVERSED, REVERSAL_WOULD_NEGATIVE_STOCK, ANCHOR_RECONCILIATION_REQUIRED, FUTURE_RECEIPT, INVALID_REVERSAL_TARGET, REVERSAL_TIME_INVALID.

### POST /test/scenarios

인증 principal의 플랫폼 test_admin 권한 필요. 새 scope·호출자 membership·bootstrap job·outbox 생성. 가상 모델/품목/정책은 시드 프로필에서 등록하고 모델 검증 후 ready. 클라이언트 파일 경로·임의 모델 URI는 받지 않는다.

권한: test_admin. 연관 테이블: `scope`, `membership`, `job`, `outbox`, `item`, `model_version`, `ordering_policy`.

요청 경로·쿼리: 없음.

요청 본문: `ScenarioRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "name": "6주 가상 물류 평가",
  "seed": 20261021,
  "hospital_code": "SYN-HOSP",
  "warehouse_code": "SYN-WH",
  "first_week_start": "2026-10-19",
  "initial_history_days": 112,
  "profile": "timesfm30_v1"
}
```

성공 응답: HTTP 202, `ScenarioAccepted`.

```json
{
  "scenario_id": "00000000-0000-4000-8000-000000000001",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: TEST_PROFILE_DISABLED, MODEL_PROFILE_UNAVAILABLE.

### GET /test/scenarios/{scenario_id}

가상 시나리오 진행 상태

권한: test_operator. 연관 테이블: `scope`, `job`, `model_version`.

요청 경로·쿼리: path `scenario_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `Scenario`.

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "name": "6주 가상 물류 평가",
  "status": "ready",
  "seed": 20261021,
  "version": 1,
  "simulation_at": "2026-10-19T00:00:00+09:00",
  "next_week_start": "2026-10-19",
  "model_version_id": "00000000-0000-4000-8000-000000000005",
  "last_job_id": "00000000-0000-4000-8000-000000000007",
  "active_week_start": null,
  "last_completed_week_start": null,
  "blocking_code": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /test/scenarios/{scenario_id}/weekly-deliveries

scope 행 잠금으로 expected_version·next_week_start 확인. simulate 작업이 일별 소비·도착·만료를 적용하고 일반 주간 제출 경로로 전달한다. 같은 키는 같은 작업. 다른 키라도 같은 주간은 기존 작업 반환하며 주차가 자동으로 두 번 진행되지 않는다. simulator_only 미래 정답은 ML 권한에서 제외.

권한: test_operator. 연관 테이블: `scope`, `inventory_event`, `receipt`, `delivery`, `job`, `outbox`.

요청 경로·쿼리: path `scenario_id` (필수).

요청 본문: `AdvanceRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "expected_version": 1,
  "week_start": "2026-10-19"
}
```

성공 응답: HTTP 202, `Accepted`.

```json
{
  "resource_id": "00000000-0000-4000-8000-000000000001",
  "job_id": "00000000-0000-4000-8000-000000000007",
  "status": "queued",
  "status_url": "/api/v1/jobs/00000000-0000-4000-8000-000000000007",
  "request_id": "00000000-0000-4000-8000-000000000017"
}
```

오류 상태: 202, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: SCENARIO_BUSY, SCENARIO_VERSION_CONFLICT, WEEK_ALREADY_ADVANCED.

### GET /logs

로그 유실·30일 보존을 전제로 한다. 업무 감사 원장으로 사용하지 않는다.

권한: log_reader. 연관 테이블: `app_log`.

요청 경로·쿼리: query `limit` (선택); query `cursor` (선택); query `job_id` (선택); query `level` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `LogPage`.

```json
{
  "items": [
    {
      "event_id": "00000000-0000-4000-8000-000000000016",
      "occurred_at": "2026-10-21T03:00:10Z",
      "ingested_at": "2026-10-21T03:00:11Z",
      "level": "INFO",
      "service": "timesfm",
      "code": "FORECAST_COMPLETED",
      "message": "가상 예측 완료",
      "correlation_id": "00000000-0000-4000-8000-000000000017",
      "job_id": "00000000-0000-4000-8000-000000000007"
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /health/live

API 프로세스 생존

권한: public. 연관 테이블: 없음.

요청 경로·쿼리: 없음.

요청 본문: 없음.

성공 응답: HTTP 200, `Health`.

```json
{
  "status": "ok",
  "checks": {
    "database": "not_checked",
    "artifact_storage": "not_checked",
    "broker": "not_checked",
    "schema": "not_checked"
  },
  "request_id": "00000000-0000-4000-8000-000000000007"
}
```

오류 상태: 200, 400, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /health/ready

플랫폼 test_admin 또는 사전 등록한 동일 관리 권한의 상태 검사 서비스만 호출. DB·원본 볼륨·schema가 필수. 브로커만 장애이면 degraded 200으로 접수하고 Outbox 대기. 필수 의존성 불가는 503 Health 본문. 모델 준비 여부는 시나리오/예측 작업 상태에서 확인.

권한: test_admin. 연관 테이블: 없음.

요청 경로·쿼리: 없음.

요청 본문: 없음.

성공 응답: HTTP 200, `Health`.

```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "artifact_storage": "ok",
    "broker": "ok",
    "schema": "ok"
  },
  "request_id": "00000000-0000-4000-8000-000000000007"
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /model-versions/{model_version_id}/input-contract

실제 응답에는 등록된 전체 품목·69개 환자·16개 알려진 특징을 반환한다. 코드·환자 원문·가중치 경로는 반환하지 않는다.

권한: viewer, uploader. 연관 테이블: `model_version`, `model_item`, `model_feature`.

요청 경로·쿼리: path `model_version_id` (필수).

요청 본문: 없음.

성공 응답: HTTP 200, `ModelContract`.

```json
{
  "id": "00000000-0000-4000-8000-000000000005",
  "status": "validated",
  "horizon_days": 14,
  "drug_context_days": 112,
  "material_context_days": 56,
  "patient_context_days": 28,
  "expected_drugs": 20,
  "expected_materials": 10,
  "expected_patients": 69,
  "expected_known": 16,
  "items": [
    {
      "item_id": "10000000-0000-4000-8000-000000000001",
      "cohort": "drug",
      "ordinal": 0,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000002",
      "cohort": "drug",
      "ordinal": 1,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000003",
      "cohort": "drug",
      "ordinal": 2,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000004",
      "cohort": "drug",
      "ordinal": 3,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000005",
      "cohort": "drug",
      "ordinal": 4,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000006",
      "cohort": "drug",
      "ordinal": 5,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000007",
      "cohort": "drug",
      "ordinal": 6,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000008",
      "cohort": "drug",
      "ordinal": 7,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000009",
      "cohort": "drug",
      "ordinal": 8,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000010",
      "cohort": "drug",
      "ordinal": 9,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000011",
      "cohort": "drug",
      "ordinal": 10,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000012",
      "cohort": "drug",
      "ordinal": 11,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000013",
      "cohort": "drug",
      "ordinal": 12,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000014",
      "cohort": "drug",
      "ordinal": 13,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000015",
      "cohort": "drug",
      "ordinal": 14,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000016",
      "cohort": "drug",
      "ordinal": 15,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000017",
      "cohort": "drug",
      "ordinal": 16,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000018",
      "cohort": "drug",
      "ordinal": 17,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000019",
      "cohort": "drug",
      "ordinal": 18,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000020",
      "cohort": "drug",
      "ordinal": 19,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000021",
      "cohort": "material",
      "ordinal": 0,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000022",
      "cohort": "material",
      "ordinal": 1,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000023",
      "cohort": "material",
      "ordinal": 2,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000024",
      "cohort": "material",
      "ordinal": 3,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000025",
      "cohort": "material",
      "ordinal": 4,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000026",
      "cohort": "material",
      "ordinal": 5,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000027",
      "cohort": "material",
      "ordinal": 6,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000028",
      "cohort": "material",
      "ordinal": 7,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000029",
      "cohort": "material",
      "ordinal": 8,
      "unit": "개"
    },
    {
      "item_id": "10000000-0000-4000-8000-000000000030",
      "cohort": "material",
      "ordinal": 9,
      "unit": "개"
    }
  ],
  "features": [
    {
      "kind": "patient",
      "feature_key": "total__ALL",
      "ordinal": 0,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sex__M",
      "ordinal": 1,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sex__F",
      "ordinal": 2,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sex__OTHER",
      "ordinal": 3,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "encounter__IN",
      "ordinal": 4,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "encounter__OUT",
      "ordinal": 5,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "encounter__OTHER",
      "ordinal": 6,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__0_9",
      "ordinal": 7,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__10_19",
      "ordinal": 8,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__20_29",
      "ordinal": 9,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__30_39",
      "ordinal": 10,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__40_49",
      "ordinal": 11,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__50_59",
      "ordinal": 12,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__60_69",
      "ordinal": 13,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__70_79",
      "ordinal": 14,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__80_89",
      "ordinal": 15,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__90_99",
      "ordinal": 16,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__100_109",
      "ordinal": 17,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__110_119",
      "ordinal": 18,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__120_129",
      "ordinal": 19,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "age__MISSING_INVALID",
      "ordinal": 20,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": true,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__I",
      "ordinal": 21,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__II",
      "ordinal": 22,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__III",
      "ordinal": 23,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__IV",
      "ordinal": 24,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__V",
      "ordinal": 25,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__VI",
      "ordinal": 26,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__VII",
      "ordinal": 27,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__VIII",
      "ordinal": 28,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__IX",
      "ordinal": 29,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__X",
      "ordinal": 30,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XI",
      "ordinal": 31,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XII",
      "ordinal": 32,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XIII",
      "ordinal": 33,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XIV",
      "ordinal": 34,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XV",
      "ordinal": 35,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XVI",
      "ordinal": 36,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XVII",
      "ordinal": 37,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XVIII",
      "ordinal": 38,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XIX",
      "ordinal": 39,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XX",
      "ordinal": 40,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XXI",
      "ordinal": 41,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__XXII",
      "ordinal": 42,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__MISSING",
      "ordinal": 43,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": true,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "main_kcd__INVALID",
      "ordinal": 44,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": true,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__I",
      "ordinal": 45,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__II",
      "ordinal": 46,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__III",
      "ordinal": 47,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__IV",
      "ordinal": 48,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__V",
      "ordinal": 49,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__VI",
      "ordinal": 50,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__VII",
      "ordinal": 51,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__VIII",
      "ordinal": 52,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__IX",
      "ordinal": 53,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__X",
      "ordinal": 54,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XI",
      "ordinal": 55,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XII",
      "ordinal": 56,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XIII",
      "ordinal": 57,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XIV",
      "ordinal": 58,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XV",
      "ordinal": 59,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XVI",
      "ordinal": 60,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XVII",
      "ordinal": 61,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XVIII",
      "ordinal": 62,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XIX",
      "ordinal": 63,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XX",
      "ordinal": 64,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XXI",
      "ordinal": 65,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__XXII",
      "ordinal": 66,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__MISSING",
      "ordinal": 67,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": true,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "patient",
      "feature_key": "sub_kcd__INVALID",
      "ordinal": 68,
      "definition": {
        "aggregation": "연구 입력 계약과 같은 일별 집계",
        "missing_category": true,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "target_public_holiday",
      "ordinal": 0,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "target_named_holiday",
      "ordinal": 1,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "target_before_named_holiday",
      "ordinal": 2,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "target_after_named_holiday",
      "ordinal": 3,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "target_weekend",
      "ordinal": 4,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "is_national_epidemic_distancing_response",
      "ordinal": 5,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "last_day_distancing_response",
      "ordinal": 6,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_0",
      "ordinal": 7,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_1",
      "ordinal": 8,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_2",
      "ordinal": 9,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_3",
      "ordinal": 10,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_4",
      "ordinal": 11,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_5",
      "ordinal": 12,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "weekday_6",
      "ordinal": 13,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "year_sin",
      "ordinal": 14,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    },
    {
      "kind": "known",
      "feature_key": "year_cos",
      "ordinal": 15,
      "definition": {
        "aggregation": "원점 당시 알려진 달력",
        "missing_category": false,
        "source_contract": "TIMESFM_VECTOR_PLAN"
      }
    }
  ]
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### GET /forecasts/{forecast_id}/evaluations

가용한 제출 실제값으로 계산한 최신 truth_hash 결과. 수정 전 평가도 보존하며 truth_hash 쿼리로 지정 가능. 아직 평가가 없으면 빈 목록. 미래 합성 정답을 앞서 조회하지 않는다.

권한: viewer. 연관 테이블: `forecast_evaluation`, `evaluation_delivery`.

요청 경로·쿼리: path `forecast_id` (필수); query `limit` (선택); query `cursor` (선택); query `window` (선택); query `truth_hash` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `ForecastEvaluationPage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000016",
      "forecast_id": "00000000-0000-4000-8000-000000000009",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "window": "week1",
      "truth_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "status": "ready",
      "observations": 7,
      "expected_labels": 7,
      "mae": "1.5",
      "bias": "-0.5",
      "reason_code": null,
      "evaluated_at": "2026-10-21T03:30:00Z"
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

### POST /purchase-orders/{order_id}/release

blocked이며 submit 시도 이력이 전혀 없거나 공급처의 명시적 최종 rejected일 때만 허용. sending/unknown/accepted 및 전송 이력이 있고 단순 not_found만 있는 경우 금지. scope·order 잠금 후 토큰 무효화·submit 작업 차단·예약 해제·종료 근거·이력·응답을 원자적으로 저장. 늦은 큐 메시지는 전송하지 않음. 새 주문은 재계산·새 승인을 거친다.

권한: operator. 연관 테이블: `purchase_order`, `order_attempt`, `order_reservation`, `job`, `outbox`, `business_event`.

요청 경로·쿼리: path `order_id` (필수).

요청 본문: `ReleaseRequest`. 필드별 자료형·필수 여부는 아래 스키마 사전과 OpenAPI를 따른다.

```json
{
  "expected_status": "blocked",
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "reason": "입력 정정 후 미전송 주문 의도 종료"
}
```

성공 응답: HTTP 200, `PurchaseOrder`.

```json
{
  "id": "00000000-0000-4000-8000-000000000012",
  "decision_id": "00000000-0000-4000-8000-000000000011",
  "supplier_id": "00000000-0000-4000-8000-000000000003",
  "order_key": "00000000-0000-4000-8000-000000000012",
  "status": "voided",
  "receipt_status": "not_received",
  "content_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "released_at": "2026-10-26T00:00:00+09:00",
  "supplier_order_ref": null,
  "accepted_at": null,
  "lines": [
    {
      "id": "00000000-0000-4000-8000-000000000013",
      "proposal_line_id": "00000000-0000-4000-8000-000000000017",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "unit": "개",
      "packs": 5,
      "ordered_units": "50",
      "received_units": "0",
      "remaining_units": "0",
      "expected_at": "2026-10-28T00:00:00+09:00"
    }
  ]
}
```

오류 상태: 200, 400, 401, 403, 404, 409, 422, 500, 503 (첫 값은 성공). 업무 오류: ORDER_RELEASE_UNSAFE, ORDER_STATE_CONFLICT.

### GET /test/scenarios/{scenario_id}/demand-outcomes

진행 완료된 과거 가상 일자만 조회. 미래 합성 정답은 반환하지 않는다. requested=fulfilled+lost이며 물리 사용량과 미충족 요청량을 구분한다.

권한: test_operator. 연관 테이블: `demand_outcome`, `inventory_event`, `scope`.

요청 경로·쿼리: path `scenario_id` (필수); query `limit` (선택); query `cursor` (선택); query `item_id` (선택); query `date_from` (선택); query `date_to` (선택).

요청 본문: 없음.

성공 응답: HTTP 200, `DemandOutcomePage`.

```json
{
  "items": [
    {
      "id": "00000000-0000-4000-8000-000000000016",
      "item_id": "10000000-0000-4000-8000-000000000001",
      "day": "2026-10-19",
      "occurred_at": "2026-10-19T09:00:00+09:00",
      "requested_units": "10",
      "fulfilled_units": "7",
      "lost_units": "3",
      "unit": "개",
      "inventory_version": 2
    }
  ],
  "next_cursor": null
}
```

오류 상태: 200, 400, 401, 403, 404, 422, 500, 503 (첫 값은 성공). 업무 오류: 공통 인증·범위·자원·의존성 오류.

## 스키마 사전

각 객체는 명시한 필드 외 입력을 거절한다. nullable은 필드 생략과 다르다. 목록·상세 응답도 OpenAPI와 동일한 필드를 제공한다.

### FieldError

| 필드 | 필수 | 정의 |
|---|---|---|
| `path` | 예 | `{"type":"string"}` |
| `code` | 예 | `{"type":"string"}` |
| `message` | 예 | `{"type":"string"}` |

### ErrorDetail

| 필드 | 필수 | 정의 |
|---|---|---|
| `code` | 예 | `{"type":"string"}` |
| `message` | 예 | `{"type":"string"}` |
| `retryable` | 예 | `{"type":"boolean"}` |
| `fields` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/FieldError"}}` |

### Error

| 필드 | 필수 | 정의 |
|---|---|---|
| `request_id` | 예 | `{"type":"string","format":"uuid"}` |
| `error` | 예 | `{"$ref":"#/components/schemas/ErrorDetail"}` |

### Accepted

| 필드 | 필수 | 정의 |
|---|---|---|
| `resource_id` | 예 | `{"type":"string","format":"uuid"}` |
| `job_id` | 예 | `{"type":"string","format":"uuid"}` |
| `status` | 예 | `{"type":"string","enum":["queued","dispatch_pending"]}` |
| `status_url` | 예 | `{"type":"string"}` |
| `request_id` | 예 | `{"type":"string","format":"uuid"}` |

### Item

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `code` | 예 | `{"type":"string"}` |
| `name` | 예 | `{"type":"string"}` |
| `kind` | 예 | `{"type":"string","enum":["drug","material"]}` |
| `base_unit` | 예 | `{"type":"string"}` |
| `target_semantics` | 예 | `{"type":"string","enum":["drug_record_units","material_issue_units"]}` |
| `active` | 예 | `{"type":"boolean"}` |
| `current_policy_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |

### CalendarRule

| 필드 | 필수 | 정의 |
|---|---|---|
| `mode` | 예 | `{"type":"string","enum":["calendar_days"]}` |
| `business_weekdays` | 예 | `{"type":"array","items":{"type":"integer","minimum":1,"maximum":7},"minItems":1,"maxItems":7,"uniqueItems":true,"const":[1,2,3,4,5,6,7]}` |
| `cutoff_local` | 예 | `{"type":"string","pattern":"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"}` |
| `timezone` | 예 | `{"type":"string","const":"Asia/Seoul"}` |
| `arrival_local` | 예 | `{"type":"string","const":"00:00"}` |

### OrderingPolicyRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `expected_version` | 예 | `{"type":"integer","minimum":0}` |
| `supplier_id` | 예 | `{"type":"string","format":"uuid"}` |
| `lead_days` | 예 | `{"type":"integer","minimum":1}` |
| `review_days` | 예 | `{"type":"integer","const":7}` |
| `pack_size` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열","not":{"pattern":"^0(?:\\.0+)?$"}}` |
| `minimum_packs` | 예 | `{"type":"integer","minimum":1}` |
| `safety_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `valid_from` | 예 | `{"type":"string","format":"date-time"}` |
| `calendar_rule` | 예 | `{"$ref":"#/components/schemas/CalendarRule"}` |

### OrderingPolicy

| 필드 | 필수 | 정의 |
|---|---|---|
| `supplier_id` | 예 | `{"type":"string","format":"uuid"}` |
| `lead_days` | 예 | `{"type":"integer","minimum":1}` |
| `review_days` | 예 | `{"type":"integer","const":7}` |
| `pack_size` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열","not":{"pattern":"^0(?:\\.0+)?$"}}` |
| `minimum_packs` | 예 | `{"type":"integer","minimum":1}` |
| `safety_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `valid_from` | 예 | `{"type":"string","format":"date-time"}` |
| `calendar_rule` | 예 | `{"$ref":"#/components/schemas/CalendarRule"}` |
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `version` | 예 | `{"type":"integer","minimum":1}` |
| `policy_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |

### Inventory

| 필드 | 필수 | 정의 |
|---|---|---|
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `unit` | 예 | `{"type":"string"}` |
| `as_of` | 예 | `{"type":"string","format":"date-time"}` |
| `version` | 예 | `{"type":"integer","minimum":1}` |
| `on_hand` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `unavailable` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `allocated` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `available_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `confirmed_incoming_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `pending_order_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `unknown_order_ids` | 예 | `{"type":"array","items":{"type":"string","format":"uuid"}}` |
| `blocking_codes` | 예 | `{"type":"array","items":{"type":"string"}}` |

### UsageDay

| 필드 | 필수 | 정의 |
|---|---|---|
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `day` | 예 | `{"type":"string","format":"date"}` |
| `quantity` | 예 | `{"anyOf":[{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"},{"type":"null"}]}` |
| `observation` | 예 | `{"type":"string","enum":["observed","missing","not_applicable"]}` |
| `unit` | 예 | `{"type":"string"}` |
| `available_at` | 예 | `{"type":"string","format":"date-time"}` |

### PatientDay

| 필드 | 필수 | 정의 |
|---|---|---|
| `feature_key` | 예 | `{"type":"string"}` |
| `day` | 예 | `{"type":"string","format":"date"}` |
| `value` | 예 | `{"anyOf":[{"type":"integer","minimum":0},{"type":"null"}]}` |
| `observation` | 예 | `{"type":"string","enum":["observed","missing"]}` |
| `available_at` | 예 | `{"type":"string","format":"date-time"}` |

### StockLine

| 필드 | 필수 | 정의 |
|---|---|---|
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `on_hand` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `unavailable` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `allocated` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `unit` | 예 | `{"type":"string"}` |

### Stock

| 필드 | 필수 | 정의 |
|---|---|---|
| `as_of` | 예 | `{"type":"string","format":"date-time"}` |
| `source_version` | 예 | `{"type":"string","minLength":1,"maxLength":128}` |
| `lines` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/StockLine"},"minItems":1,"maxItems":100}` |

### OpenOrderLine

| 필드 | 필수 | 정의 |
|---|---|---|
| `supplier_id` | 예 | `{"type":"string","format":"uuid"}` |
| `source_order_key` | 예 | `{"type":"string","minLength":1,"maxLength":128}` |
| `source_line_key` | 예 | `{"type":"string","minLength":1,"maxLength":128}` |
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `remaining_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `unit` | 예 | `{"type":"string"}` |
| `expected_at` | 예 | `{"type":"string","format":"date-time"}` |
| `status` | 예 | `{"type":"string","enum":["open","cancelled","unknown"]}` |

### WeeklyDeliveryRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `schema_version` | 예 | `{"type":"integer","const":1}` |
| `week_start` | 예 | `{"type":"string","format":"date"}` |
| `week_end_exclusive` | 예 | `{"type":"string","format":"date"}` |
| `source_revision` | 예 | `{"type":"integer","minimum":1}` |
| `supersedes_delivery_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `source_closed_at` | 예 | `{"type":"string","format":"date-time"}` |
| `source_available_at` | 예 | `{"type":"string","format":"date-time"}` |
| `is_complete` | 예 | `{"type":"boolean","const":true}` |
| `model_version_id` | 예 | `{"type":"string","format":"uuid"}` |
| `usage` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/UsageDay"},"minItems":1,"maxItems":10000}` |
| `patients` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/PatientDay"},"minItems":1,"maxItems":10000}` |
| `inventory` | 예 | `{"$ref":"#/components/schemas/Stock"}` |
| `open_orders` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/OpenOrderLine"},"maxItems":1000}` |

### Delivery

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `kind` | 예 | `{"type":"string","enum":["bootstrap","weekly"]}` |
| `week_start` | 예 | `{"type":"string","format":"date"}` |
| `week_end_exclusive` | 예 | `{"type":"string","format":"date"}` |
| `source_revision` | 예 | `{"type":"integer","minimum":1}` |
| `supersedes_delivery_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `source_closed_at` | 예 | `{"type":"string","format":"date-time"}` |
| `source_available_at` | 예 | `{"type":"string","format":"date-time"}` |
| `received_at` | 예 | `{"type":"string","format":"date-time"}` |
| `status` | 예 | `{"type":"string","enum":["received","validating","validated","rejected","superseded"]}` |
| `job_id` | 예 | `{"type":"string","format":"uuid"}` |
| `snapshot_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `artifact_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `issue_count` | 예 | `{"type":"integer","minimum":0}` |
| `correction_impacts` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/CorrectionImpact"}}` |

### ValidationIssue

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `code` | 예 | `{"type":"string"}` |
| `field_path` | 예 | `{"type":"string"}` |
| `severity` | 예 | `{"type":"string","enum":["warning","error"]}` |
| `item_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `message` | 예 | `{"type":"string"}` |

### JobResult

| 필드 | 필수 | 정의 |
|---|---|---|
| `delivery_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `snapshot_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `forecast_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `proposal_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `order_ids` | 예 | `{"type":"array","items":{"type":"string","format":"uuid"}}` |
| `receipt_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `child_job_ids` | 예 | `{"type":"array","items":{"type":"string","format":"uuid"}}` |
| `required_resubmission_delivery_ids` | 예 | `{"type":"array","items":{"type":"string","format":"uuid"}}` |

### Job

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `parent_job_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `stage` | 예 | `{"type":"string","enum":["bootstrap","ingest","forecast","proposal","submit","reconcile","receipt","simulate","evaluate"]}` |
| `status` | 예 | `{"type":"string","enum":["queued","running","succeeded","retry_wait","failed","blocked"]}` |
| `current_attempt` | 예 | `{"type":"integer","minimum":0,"maximum":4}` |
| `heartbeat_at` | 예 | `{"anyOf":[{"type":"string","format":"date-time"},{"type":"null"}]}` |
| `retry_at` | 예 | `{"anyOf":[{"type":"string","format":"date-time"},{"type":"null"}]}` |
| `error_code` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |
| `retryable` | 예 | `{"type":"boolean"}` |
| `result` | 예 | `{"$ref":"#/components/schemas/JobResult"}` |
| `updated_at` | 예 | `{"type":"string","format":"date-time"}` |

### RetryRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `expected_attempt` | 예 | `{"type":"integer","minimum":0,"maximum":4}` |
| `reason` | 예 | `{"type":"string","minLength":1,"maxLength":256}` |

### Forecast

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `snapshot_id` | 예 | `{"type":"string","format":"uuid"}` |
| `model_version_id` | 예 | `{"type":"string","format":"uuid"}` |
| `forecast_origin` | 예 | `{"type":"string","format":"date"}` |
| `input_cutoff_at` | 예 | `{"type":"string","format":"date-time"}` |
| `knowledge_as_of` | 예 | `{"type":"string","format":"date-time"}` |
| `generated_at` | 예 | `{"anyOf":[{"type":"string","format":"date-time"},{"type":"null"}]}` |
| `horizon_days` | 예 | `{"type":"integer","const":14}` |
| `status` | 예 | `{"type":"string","enum":["pending","running","succeeded","failed"]}` |
| `validity` | 예 | `{"type":"string","enum":["current","input_revised"]}` |
| `job_id` | 예 | `{"type":"string","format":"uuid"}` |
| `expected_values` | 예 | `{"type":"integer","minimum":0}` |
| `stored_values` | 예 | `{"type":"integer","minimum":0}` |

### ForecastValue

| 필드 | 필수 | 정의 |
|---|---|---|
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_name` | 예 | `{"type":"string"}` |
| `unit` | 예 | `{"type":"string"}` |
| `target_day` | 예 | `{"type":"string","format":"date"}` |
| `horizon_day` | 예 | `{"type":"integer","minimum":1,"maximum":14}` |
| `prediction_raw` | 예 | `{"type":"string","pattern":"^-?(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$"}` |
| `prediction` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `quantiles` | 예 | `{"type":"array","items":{"type":"string","pattern":"^-?(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$"},"minItems":9,"maxItems":9}` |

### ProposalLine

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_name` | 예 | `{"type":"string"}` |
| `unit` | 예 | `{"type":"string"}` |
| `policy_id` | 예 | `{"type":"string","format":"uuid"}` |
| `inventory_version` | 예 | `{"type":"integer","minimum":1}` |
| `protection_end` | 예 | `{"type":"string","format":"date"}` |
| `forecast_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `safety_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `available_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `eligible_incoming` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `reserved_order_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `required_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `packs` | 예 | `{"type":"integer","minimum":0}` |
| `order_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `blocking_codes` | 예 | `{"type":"array","items":{"type":"string"}}` |

### Proposal

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `forecast_id` | 예 | `{"type":"string","format":"uuid"}` |
| `version` | 예 | `{"type":"integer","minimum":1}` |
| `supersedes_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `status` | 예 | `{"type":"string","enum":["pending","blocked","approved","rejected","expired","superseded"]}` |
| `content_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `decision_as_of` | 예 | `{"type":"string","format":"date-time"}` |
| `expires_at` | 예 | `{"type":"string","format":"date-time"}` |
| `next_demand_at` | 예 | `{"type":"string","format":"date-time"}` |
| `blocking_codes` | 예 | `{"type":"array","items":{"type":"string"}}` |
| `lines` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ProposalLine"}}` |

### RecalculateRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `expected_version` | 예 | `{"type":"integer","minimum":1}` |
| `reason` | 예 | `{"type":"string","minLength":1,"maxLength":256}` |

### DecisionRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `proposal_version` | 예 | `{"type":"integer","minimum":1}` |
| `content_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `decision` | 예 | `{"type":"string","enum":["approve","reject"]}` |
| `reason` | 아니요 | `{"type":"string","maxLength":256}` |

### Decision

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `proposal_id` | 예 | `{"type":"string","format":"uuid"}` |
| `proposal_version` | 예 | `{"type":"integer","minimum":1}` |
| `content_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `decision` | 예 | `{"type":"string","enum":["approve","reject"]}` |
| `decided_by` | 예 | `{"type":"string","format":"uuid"}` |
| `decided_at` | 예 | `{"type":"string","format":"date-time"}` |
| `order_ids` | 예 | `{"type":"array","items":{"type":"string","format":"uuid"}}` |
| `dispatch_status` | 예 | `{"type":"string","enum":["not_required","queued","dispatch_pending"]}` |

### OrderLine

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `proposal_line_id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `unit` | 예 | `{"type":"string"}` |
| `packs` | 예 | `{"type":"integer","minimum":1}` |
| `ordered_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `received_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `remaining_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `expected_at` | 예 | `{"type":"string","format":"date-time"}` |

### PurchaseOrder

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `decision_id` | 예 | `{"type":"string","format":"uuid"}` |
| `supplier_id` | 예 | `{"type":"string","format":"uuid"}` |
| `order_key` | 예 | `{"type":"string","format":"uuid"}` |
| `status` | 예 | `{"type":"string","enum":["pending_send","sending","accepted","submission_unknown","rejected","blocked","voided"]}` |
| `receipt_status` | 예 | `{"type":"string","enum":["not_received","partially_received","received"]}` |
| `content_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `released_at` | 예 | `{"anyOf":[{"type":"string","format":"date-time"},{"type":"null"}]}` |
| `supplier_order_ref` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |
| `accepted_at` | 예 | `{"anyOf":[{"type":"string","format":"date-time"},{"type":"null"}]}` |
| `lines` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/OrderLine"}}` |

### ReconcileRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `reason` | 예 | `{"type":"string","minLength":1,"maxLength":256}` |

### ReceiptLineRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `order_line_id` | 예 | `{"type":"string","format":"uuid"}` |
| `quantity` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열","not":{"pattern":"^0(?:\\.0+)?$"}}` |
| `unit` | 예 | `{"type":"string"}` |

### ReceiptRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `source_receipt_key` | 예 | `{"type":"string","minLength":1,"maxLength":128}` |
| `occurred_at` | 예 | `{"type":"string","format":"date-time"}` |
| `lines` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ReceiptLineRequest"},"minItems":1,"maxItems":100}` |

### ReceiptReversalRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `source_receipt_key` | 예 | `{"type":"string","minLength":1,"maxLength":128}` |
| `occurred_at` | 예 | `{"type":"string","format":"date-time"}` |
| `reason` | 예 | `{"type":"string","minLength":1,"maxLength":256}` |

### Receipt

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `order_id` | 예 | `{"type":"string","format":"uuid"}` |
| `source_receipt_key` | 예 | `{"type":"string"}` |
| `occurred_at` | 예 | `{"type":"string","format":"date-time"}` |
| `status` | 예 | `{"type":"string","enum":["received","applied","rejected"]}` |
| `reverses_receipt_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `job_id` | 예 | `{"type":"string","format":"uuid"}` |
| `lines` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ReceiptLineRequest"}}` |

### ScenarioRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `name` | 예 | `{"type":"string","minLength":1,"maxLength":128}` |
| `seed` | 예 | `{"type":"integer"}` |
| `hospital_code` | 예 | `{"type":"string","minLength":1,"maxLength":64}` |
| `warehouse_code` | 예 | `{"type":"string","minLength":1,"maxLength":64}` |
| `first_week_start` | 예 | `{"type":"string","format":"date"}` |
| `initial_history_days` | 예 | `{"type":"integer","minimum":112,"maximum":730}` |
| `profile` | 예 | `{"type":"string","enum":["timesfm30_v1","smoke_v1"]}` |

### Scenario

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `name` | 예 | `{"type":"string"}` |
| `status` | 예 | `{"type":"string","enum":["bootstrapping","ready","advancing","failed"]}` |
| `seed` | 예 | `{"type":"integer"}` |
| `version` | 예 | `{"type":"integer","minimum":1}` |
| `simulation_at` | 예 | `{"type":"string","format":"date-time"}` |
| `next_week_start` | 예 | `{"type":"string","format":"date"}` |
| `model_version_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `last_job_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `active_week_start` | 예 | `{"anyOf":[{"type":"string","format":"date"},{"type":"null"}]}` |
| `last_completed_week_start` | 예 | `{"anyOf":[{"type":"string","format":"date"},{"type":"null"}]}` |
| `blocking_code` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### ScenarioAccepted

| 필드 | 필수 | 정의 |
|---|---|---|
| `scenario_id` | 예 | `{"type":"string","format":"uuid"}` |
| `job_id` | 예 | `{"type":"string","format":"uuid"}` |
| `status` | 예 | `{"type":"string","enum":["queued","dispatch_pending"]}` |
| `status_url` | 예 | `{"type":"string"}` |
| `request_id` | 예 | `{"type":"string","format":"uuid"}` |

### AdvanceRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `expected_version` | 예 | `{"type":"integer","minimum":1}` |
| `week_start` | 예 | `{"type":"string","format":"date"}` |

### Log

| 필드 | 필수 | 정의 |
|---|---|---|
| `event_id` | 예 | `{"type":"string","format":"uuid"}` |
| `occurred_at` | 예 | `{"type":"string","format":"date-time"}` |
| `ingested_at` | 예 | `{"type":"string","format":"date-time"}` |
| `level` | 예 | `{"type":"string","enum":["DEBUG","INFO","WARNING","ERROR","CRITICAL"]}` |
| `service` | 예 | `{"type":"string"}` |
| `code` | 예 | `{"type":"string"}` |
| `message` | 예 | `{"type":"string"}` |
| `correlation_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `job_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |

### HealthChecks

| 필드 | 필수 | 정의 |
|---|---|---|
| `database` | 예 | `{"type":"string","enum":["ok","failed","not_checked"]}` |
| `artifact_storage` | 예 | `{"type":"string","enum":["ok","failed","not_checked"]}` |
| `broker` | 예 | `{"type":"string","enum":["ok","degraded","not_checked"]}` |
| `schema` | 예 | `{"type":"string","enum":["ok","failed","not_checked"]}` |

### Health

| 필드 | 필수 | 정의 |
|---|---|---|
| `status` | 예 | `{"type":"string","enum":["ok","degraded","not_ready"]}` |
| `checks` | 예 | `{"$ref":"#/components/schemas/HealthChecks"}` |
| `request_id` | 예 | `{"type":"string","format":"uuid"}` |

### ItemPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Item"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### OrderingPolicyPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/OrderingPolicy"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### InventoryPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Inventory"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### DeliveryPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Delivery"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### ValidationIssuePage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ValidationIssue"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### JobPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Job"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### ForecastPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Forecast"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### ForecastValuePage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ForecastValue"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### ProposalPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Proposal"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### PurchaseOrderPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/PurchaseOrder"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### LogPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/Log"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### ModelItemContract

| 필드 | 필수 | 정의 |
|---|---|---|
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `cohort` | 예 | `{"type":"string","enum":["drug","material"]}` |
| `ordinal` | 예 | `{"type":"integer","minimum":0}` |
| `unit` | 예 | `{"type":"string"}` |

### FeatureDefinition

| 필드 | 필수 | 정의 |
|---|---|---|
| `aggregation` | 예 | `{"type":"string"}` |
| `missing_category` | 예 | `{"type":"boolean"}` |
| `source_contract` | 예 | `{"type":"string"}` |

### FeatureContract

| 필드 | 필수 | 정의 |
|---|---|---|
| `kind` | 예 | `{"type":"string","enum":["patient","known"]}` |
| `feature_key` | 예 | `{"type":"string"}` |
| `ordinal` | 예 | `{"type":"integer","minimum":0}` |
| `definition` | 예 | `{"$ref":"#/components/schemas/FeatureDefinition"}` |

### ModelContract

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `status` | 예 | `{"type":"string","enum":["registered","validated","disabled"]}` |
| `horizon_days` | 예 | `{"type":"integer","const":14}` |
| `drug_context_days` | 예 | `{"type":"integer","const":112}` |
| `material_context_days` | 예 | `{"type":"integer","const":56}` |
| `patient_context_days` | 예 | `{"type":"integer","const":28}` |
| `expected_drugs` | 예 | `{"type":"integer","minimum":1}` |
| `expected_materials` | 예 | `{"type":"integer","minimum":1}` |
| `expected_patients` | 예 | `{"type":"integer","const":69}` |
| `expected_known` | 예 | `{"type":"integer","const":16}` |
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ModelItemContract"}}` |
| `features` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/FeatureContract"}}` |

### ForecastEvaluation

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `forecast_id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `window` | 예 | `{"type":"string","enum":["week1","week2"]}` |
| `truth_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `status` | 예 | `{"type":"string","enum":["pending","ready","unavailable"]}` |
| `observations` | 예 | `{"type":"integer","minimum":0,"maximum":7}` |
| `expected_labels` | 예 | `{"type":"integer","const":7}` |
| `mae` | 예 | `{"anyOf":[{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"},{"type":"null"}]}` |
| `bias` | 예 | `{"anyOf":[{"type":"string","pattern":"^-?(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$"},{"type":"null"}]}` |
| `reason_code` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |
| `evaluated_at` | 예 | `{"type":"string","format":"date-time"}` |

### ForecastEvaluationPage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/ForecastEvaluation"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

### CorrectionImpact

| 필드 | 필수 | 정의 |
|---|---|---|
| `affected_delivery_id` | 예 | `{"type":"string","format":"uuid"}` |
| `snapshot_id` | 예 | `{"type":"string","format":"uuid"}` |
| `status` | 예 | `{"type":"string","enum":["waiting_resubmission","resolved","historical_only"]}` |
| `resolution_delivery_id` | 예 | `{"anyOf":[{"type":"string","format":"uuid"},{"type":"null"}]}` |
| `reason_code` | 예 | `{"type":"string"}` |

### ReleaseRequest

| 필드 | 필수 | 정의 |
|---|---|---|
| `expected_status` | 예 | `{"type":"string","enum":["blocked","rejected"]}` |
| `content_hash` | 예 | `{"type":"string","pattern":"^[0-9a-f]{64}$"}` |
| `reason` | 예 | `{"type":"string","minLength":1,"maxLength":256}` |

### DemandOutcome

| 필드 | 필수 | 정의 |
|---|---|---|
| `id` | 예 | `{"type":"string","format":"uuid"}` |
| `item_id` | 예 | `{"type":"string","format":"uuid"}` |
| `day` | 예 | `{"type":"string","format":"date"}` |
| `occurred_at` | 예 | `{"type":"string","format":"date-time"}` |
| `requested_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `fulfilled_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `lost_units` | 예 | `{"type":"string","pattern":"^(0|[1-9][0-9]{0,13})(\\.[0-9]{1,6})?$","description":"비음수 기본 단위의 정확한 소수 문자열"}` |
| `unit` | 예 | `{"type":"string"}` |
| `inventory_version` | 예 | `{"type":"integer","minimum":1}` |

### DemandOutcomePage

| 필드 | 필수 | 정의 |
|---|---|---|
| `items` | 예 | `{"type":"array","items":{"$ref":"#/components/schemas/DemandOutcome"}}` |
| `next_cursor` | 예 | `{"anyOf":[{"type":"string"},{"type":"null"}]}` |

## 오류 응답 예시

```json
{
  "request_id": "00000000-0000-4000-8000-000000000007",
  "error": {
    "code": "PROPOSAL_STALE",
    "message": "주문안 기준이 변경되었습니다.",
    "retryable": false,
    "fields": []
  }
}
```

공통 오류 예시는 응답 형식을 나타낸다. 실제 code는 상태와 원인에 맞춰 반환하며 원문 데이터·SQL·스택·인증정보를 응답에 넣지 않는다. 반환 이후 실패한 비동기 작업을 HTTP 성공으로 덮어쓰지 않는다.

## 실제 기록 프로필과 예측 이력 (2026-10-22)

`ScenarioRequest.profile=cheonan_records_v1`은 등록된 실제 일별 집계만 읽는다. `Scenario.data_source`로 실제 기록과 이전 생성 자료를 구분하고 `inventory_source=not_registered`이면 재고·발주 조건은 미등록이다. 이 프로필에 한해 inventory=null을 허용하며 임의 재고나 주문안을 만들지 않는다.

`GET /items/{item_id}/forecast-history?forecast_id={id}`는 scope·viewer 권한을 검사하고 `ForecastHistory`를 반환한다. 현재/직전 예측의 id·원점·날짜별 수량, 현재 원점 이전의 관측 실제값, 동일 관측일의 MAE·WAPE·표본 수를 포함한다. 입력이 없는 지표는 null이다. 날짜·수량·필수 필드의 기계 계약은 OpenAPI 1.2.0을 따른다. 미래 정답은 공개하지 않는다.

### 대시보드 환자 집계 조회

`GET /forecasts/{forecast_id}/patient-history`는 해당 예측에 봉인된 28일 환자 전체·입원·외래 집계만 반환한다. `X-Workspace-ID`와 viewer 권한을 검증하며 다른 범위의 예측은 404로 차단한다. 각 날짜는 `day`, `total`, `inpatient`, `outpatient`로 구성하고 미관측은 null로 보존한다. 원점과 가용 기준시각을 함께 반환하며 원점 이후 실제값이나 환자 식별값을 반환하지 않는다.

### 지난주 테스트 자료 초기화

`POST /test/scenarios/{scenario_id}/reset-last-week`

권한은 test_admin 및 해당 업무의 test_operator/admin이다. `X-Workspace-ID`는 경로의 scenario_id와 같아야 하며 `Idempotency-Key`가 필수다. 요청은 `{"expected_version":3}`, 응답은 202와 `ScenarioAccepted`(`scenario_id`, `job_id`, `status`, `status_url`, `request_id`)이다.

마지막 완료 주간의 월요일을 다음 전달 주간으로 삼아 새 테스트 업무를 bootstrap한다. 기존 업무의 원본·예측·주문·입고는 보존하고 초기화된 업무에는 아직 전달하지 않은 주간의 예측이나 주문이 없다. 주간 자료를 다시 전달하면 같은 주간으로 실제 TimesFM을 실행한다. 진행 중 작업·버전 불일치·전달 완료 주간 부재는 409이며 다른 업무 범위는 404다. 같은 키의 재전송은 동일 새 업무를 반환하고 본문 변경은 409다.
