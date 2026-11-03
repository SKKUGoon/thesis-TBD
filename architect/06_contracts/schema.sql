-- 설계용 PostgreSQL 18 DDL. 연구/실제 DB에 적용하지 않는다. 역할별 GRANT와 업무 함수는 후속 구현 대상.

BEGIN;

CREATE SCHEMA app;

CREATE DOMAIN app.sha256 AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE DOMAIN app.finite_number AS numeric(20,6) CHECK (VALUE <> 'NaN'::numeric);

CREATE DOMAIN app.quantity AS numeric(20,6) CHECK (VALUE >= 0 AND VALUE <> 'NaN'::numeric);

-- 병원·창고별 가상 시나리오와 진행 잠금 / 쓰기: 테스트 제어
CREATE TABLE app.scope (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_code text NOT NULL,
    warehouse_code text NOT NULL,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'bootstrapping',
    seed bigint NOT NULL,
    simulation_at timestamptz NOT NULL,
    next_week_start date NOT NULL,
    version bigint NOT NULL DEFAULT 1,
    profile jsonb NOT NULL,
    model_version_id uuid,
    active_job_id uuid,
    active_week_start date,
    last_completed_week_start date,
    blocking_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('bootstrapping','ready','advancing','failed')),
    CHECK (version > 0),
    CHECK (extract(isodow from next_week_start)=1)
);

-- 검증한 인증 주체의 참조 / 쓰기: 인증 관리
CREATE TABLE app.principal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject text NOT NULL UNIQUE,
    kind text NOT NULL,
    disabled boolean NOT NULL DEFAULT false,
    platform_role text NOT NULL DEFAULT 'none' CHECK (platform_role IN ('none','test_admin')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (kind IN ('human','service'))
);

-- 범위별 접근 권한 / 쓰기: 인증 관리
CREATE TABLE app.membership (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    role text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, principal_id, role),
    CHECK (role IN ('viewer','uploader','approver','operator','admin','receiver','test_operator','log_reader'))
);

-- 비공개 원본·모델·manifest 파일 참조 / 쓰기: API·등록 관리
CREATE TABLE app.artifact (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    kind text NOT NULL,
    storage_key text NOT NULL,
    sha256 app.sha256 NOT NULL,
    byte_count bigint NOT NULL,
    media_type text NOT NULL,
    access_class text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, storage_key),
    CHECK (byte_count >= 0),
    CHECK (kind IN ('delivery','model','manifest','synthetic_truth')),
    CHECK (access_class IN ('input','model','simulator_only'))
);

-- 가상 공급처 등록 / 쓰기: 관리
CREATE TABLE app.supplier (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    adapter text NOT NULL CHECK (adapter='synthetic'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, code)
);

-- 약품·물품의 고정 식별과 단위 / 쓰기: 관리
CREATE TABLE app.item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    base_unit text NOT NULL,
    target_semantics text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, code),
    CHECK ((kind='drug' AND target_semantics='drug_record_units') OR (kind='material' AND target_semantics='material_issue_units'))
);

-- 품목별 불변 발주 정책 버전 / 쓰기: 관리
CREATE TABLE app.ordering_policy (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    item_id uuid NOT NULL,
    supplier_id uuid NOT NULL,
    version integer NOT NULL,
    review_days integer NOT NULL DEFAULT 7,
    lead_days integer NOT NULL,
    pack_size app.quantity NOT NULL,
    minimum_packs integer NOT NULL,
    safety_units app.quantity NOT NULL,
    valid_from timestamptz NOT NULL,
    calendar_rule jsonb NOT NULL CHECK (calendar_rule->>'mode'='calendar_days'),
    policy_hash app.sha256 NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, item_id, version),
    UNIQUE (scope_id, id, item_id),
    CHECK (version > 0),
    CHECK (review_days = 7),
    CHECK (lead_days >= 1),
    CHECK (pack_size > 0),
    CHECK (minimum_packs >= 1)
);

-- 고정 모델·장치·전처리 계약 / 쓰기: 관리
CREATE TABLE app.model_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    name text NOT NULL,
    checkpoint_revision text NOT NULL,
    checkpoint_artifact_id uuid NOT NULL,
    code_commit text NOT NULL,
    image_digest text NOT NULL,
    backend text NOT NULL,
    horizon_days integer NOT NULL DEFAULT 14,
    drug_context_days integer NOT NULL DEFAULT 112,
    material_context_days integer NOT NULL DEFAULT 56,
    patient_context_days integer NOT NULL DEFAULT 28,
    expected_drugs integer NOT NULL DEFAULT 20,
    expected_materials integer NOT NULL DEFAULT 10,
    expected_patients integer NOT NULL DEFAULT 69,
    expected_known integer NOT NULL DEFAULT 16,
    manifest jsonb NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, name),
    CHECK (backend IN ('cpu','cuda','mps')),
    CHECK (status IN ('registered','validated','disabled')),
    CHECK (horizon_days=14),
    CHECK (drug_context_days=112),
    CHECK (material_context_days=56),
    CHECK (patient_context_days=28),
    CHECK (expected_drugs > 0 AND expected_materials > 0 AND expected_patients=69 AND expected_known=16)
);

-- 모델별 코호트·품목 순서 / 쓰기: 관리
CREATE TABLE app.model_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    item_id uuid NOT NULL,
    cohort text NOT NULL CHECK (cohort IN ('drug','material')),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, model_version_id, item_id),
    UNIQUE (scope_id, model_version_id, cohort, ordinal)
);

-- 69개 환자·16개 알려진 특징의 정의와 순서 / 쓰기: 관리
CREATE TABLE app.model_feature (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('patient','known')),
    feature_key text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    definition jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, model_version_id, kind, feature_key),
    UNIQUE (scope_id, model_version_id, kind, ordinal)
);

-- 주간 또는 초기 이력 제출·정정 revision / 쓰기: API·수신
CREATE TABLE app.delivery (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    kind text NOT NULL,
    week_start date NOT NULL,
    week_end_exclusive date NOT NULL,
    source_revision integer NOT NULL,
    supersedes_id uuid,
    source_closed_at timestamptz NOT NULL,
    source_available_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    schema_version integer NOT NULL DEFAULT 1,
    model_version_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    body_hash app.sha256 NOT NULL,
    status text NOT NULL,
    submitted_by uuid NOT NULL,
    validation_completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, week_start, week_end_exclusive, source_revision),
    CHECK (kind IN ('bootstrap','weekly')),
    CHECK (status IN ('received','validating','validated','rejected','superseded')),
    CHECK (source_revision > 0),
    CHECK (week_end_exclusive > week_start),
    CHECK (kind <> 'weekly' OR (week_end_exclusive=week_start+7 AND extract(isodow from week_start)=1)),
    CHECK (source_available_at >= source_closed_at)
);

-- 집계·파일·필드 검증 오류 / 쓰기: 수신
CREATE TABLE app.validation_issue (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    item_id uuid,
    code text NOT NULL,
    field_path text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('warning','error')),
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id)
);

-- 제출 revision별 일별 수량 / 쓰기: 수신
CREATE TABLE app.usage_day (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    item_id uuid NOT NULL,
    day date NOT NULL,
    quantity app.quantity,
    observation text NOT NULL,
    unit text NOT NULL,
    available_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, delivery_id, item_id, day),
    CHECK ((observation='observed' AND quantity IS NOT NULL) OR (observation IN ('missing','not_applicable') AND quantity IS NULL))
);

-- 식별값 없는 일별 환자 집계 / 쓰기: 수신
CREATE TABLE app.patient_day (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    feature_key text NOT NULL,
    day date NOT NULL,
    value bigint,
    observation text NOT NULL,
    available_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, delivery_id, feature_key, day),
    CHECK ((observation='observed' AND value IS NOT NULL AND value>=0) OR (observation='missing' AND value IS NULL))
);

-- 기준시각의 권위 있는 재고 스냅샷 / 쓰기: 수신·업무
CREATE TABLE app.inventory_anchor (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    as_of timestamptz NOT NULL,
    source_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('received','applied','rejected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, delivery_id)
);

-- 기준 재고 품목별 수량 / 쓰기: 수신
CREATE TABLE app.inventory_anchor_line (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    anchor_id uuid NOT NULL,
    item_id uuid NOT NULL,
    on_hand app.quantity NOT NULL,
    unavailable app.quantity NOT NULL,
    allocated app.quantity NOT NULL,
    unit text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, anchor_id, item_id),
    CHECK (unavailable+allocated <= on_hand)
);

-- 제출별 외부 미입고 주문 관측 / 쓰기: 수신·업무
CREATE TABLE app.external_open_order (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    supplier_id uuid NOT NULL,
    source_order_key text NOT NULL,
    source_line_key text NOT NULL,
    item_id uuid NOT NULL,
    remaining_units app.quantity NOT NULL,
    unit text NOT NULL,
    expected_at timestamptz NOT NULL,
    matched_order_line_id uuid,
    status text NOT NULL CHECK (status IN ('open','cancelled','unknown')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, delivery_id, supplier_id, source_order_key, source_line_key)
);

-- 불변 모델 입력 묶음 / 쓰기: 수신
CREATE TABLE app.input_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    forecast_origin date NOT NULL,
    cutoff_at timestamptz NOT NULL,
    knowledge_as_of timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('building','sealed','invalid')),
    manifest_hash app.sha256 NOT NULL,
    sealed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, delivery_id, model_version_id),
    UNIQUE (scope_id, id, model_version_id),
    CHECK ((status='sealed')=(sealed_at IS NOT NULL)),
    CHECK (cutoff_at <= knowledge_as_of)
);

-- 입력에 사용한 제출 revision 목록 / 쓰기: 수신
CREATE TABLE app.snapshot_source (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    start_day date NOT NULL,
    end_day_exclusive date NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, snapshot_id, delivery_id),
    CHECK (end_day_exclusive > start_day)
);

-- 실행별 동결 목표 입력 복사 / 쓰기: 수신
CREATE TABLE app.snapshot_target (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    item_id uuid NOT NULL,
    day date NOT NULL,
    quantity app.quantity,
    observation text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, snapshot_id, item_id, day),
    CHECK ((observation='observed' AND quantity IS NOT NULL) OR (observation IN ('missing','not_applicable') AND quantity IS NULL))
);

-- 동결 환자·알려진 달력 입력 / 쓰기: 수신
CREATE TABLE app.snapshot_feature (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('patient','known')),
    feature_key text NOT NULL,
    day date NOT NULL,
    value app.finite_number,
    observation text NOT NULL,
    announced_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, snapshot_id, kind, feature_key, day),
    CHECK ((observation='observed' AND value IS NOT NULL) OR (observation='missing' AND value IS NULL))
);

-- 비동기 단계별 작업과 복구 상태 / 쓰기: 담당 실행기·복구
CREATE TABLE app.job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    parent_job_id uuid,
    stage text NOT NULL,
    resource_id uuid NOT NULL,
    dedupe_key text NOT NULL,
    status text NOT NULL,
    current_attempt integer NOT NULL DEFAULT 0,
    next_retry_at timestamptz,
    error_code text,
    result jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, dedupe_key),
    CHECK (stage IN ('bootstrap','ingest','forecast','proposal','submit','reconcile','receipt','simulate','evaluate')),
    CHECK (status IN ('queued','running','succeeded','retry_wait','failed','blocked')),
    CHECK (current_attempt BETWEEN 0 AND 4)
);

-- 작업 시도와 fencing token / 쓰기: 담당 실행기·복구
CREATE TABLE app.job_attempt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    job_id uuid NOT NULL,
    number integer NOT NULL CHECK (number BETWEEN 1 AND 4),
    fencing_token uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('running','succeeded','failed','abandoned')),
    started_at timestamptz NOT NULL,
    heartbeat_at timestamptz NOT NULL,
    lease_until timestamptz NOT NULL,
    finished_at timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, job_id, number),
    UNIQUE (scope_id, fencing_token)
);

-- 업무 커밋과 결합된 전달 대기 메시지 / 쓰기: 생산 모듈·전달자
CREATE TABLE app.outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    job_id uuid NOT NULL,
    message_id uuid NOT NULL,
    attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 4),
    message_type text NOT NULL,
    routing_key text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','leased','published','held')),
    available_at timestamptz NOT NULL,
    lease_token uuid,
    lease_until timestamptz,
    published_at timestamptz,
    publish_count integer NOT NULL DEFAULT 0 CHECK (publish_count>=0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, message_id)
);

-- 소비 완료 메시지 중복 방지 / 쓰기: 각 소비자
CREATE TABLE app.inbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    consumer text NOT NULL,
    message_id uuid NOT NULL,
    job_id uuid NOT NULL,
    attempt integer NOT NULL,
    processed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, consumer, message_id)
);

-- API 재요청 응답과 본문 해시 / 쓰기: API
CREATE TABLE app.api_idempotency (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    method_path text NOT NULL,
    request_key text NOT NULL,
    request_hash app.sha256 NOT NULL,
    http_status integer NOT NULL CHECK (http_status BETWEEN 200 AND 599),
    response_body jsonb NOT NULL,
    location text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, principal_id, method_path, request_key)
);

-- 한 snapshot의 공동 예측 실행 / 쓰기: TimesFM
CREATE TABLE app.forecast_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    job_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','running','succeeded','failed')),
    validity text NOT NULL DEFAULT 'current' CHECK (validity IN ('current','input_revised')),
    generated_at timestamptz,
    manifest jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, snapshot_id),
    UNIQUE (scope_id, job_id),
    UNIQUE (scope_id, id, model_version_id)
);

-- 원점·품목·대상일별 예측 / 쓰기: TimesFM
CREATE TABLE app.forecast_value (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    forecast_id uuid NOT NULL,
    model_version_id uuid NOT NULL,
    item_id uuid NOT NULL,
    target_day date NOT NULL,
    horizon_day integer NOT NULL CHECK (horizon_day BETWEEN 1 AND 14),
    prediction_raw app.finite_number NOT NULL,
    prediction app.quantity NOT NULL,
    quantiles numeric(20,6)[] NOT NULL CHECK (cardinality(quantiles)=9),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, forecast_id, item_id, target_day)
);

-- 품목별 현재 재고 투영과 버전 / 쓰기: 수신·업무
CREATE TABLE app.inventory_state (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    item_id uuid NOT NULL,
    anchor_id uuid NOT NULL,
    as_of timestamptz NOT NULL,
    on_hand app.quantity NOT NULL,
    unavailable app.quantity NOT NULL,
    allocated app.quantity NOT NULL,
    version bigint NOT NULL DEFAULT 1 CHECK (version>0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, item_id),
    CHECK (unavailable+allocated<=on_hand)
);

-- 실제·가상 재고 사건의 불변 원장 / 쓰기: 업무
CREATE TABLE app.inventory_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    item_id uuid NOT NULL,
    source_key text NOT NULL,
    stock_effect boolean NOT NULL DEFAULT true,
    event_kind text NOT NULL,
    occurred_at timestamptz NOT NULL,
    delta_on_hand app.finite_number NOT NULL,
    receipt_line_id uuid,
    reverses_event_id uuid,
    affected_version bigint NOT NULL CHECK (affected_version>0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, source_key),
    CHECK (event_kind IN ('consumption','receipt','adjustment','reversal'))
);

-- 주문안 버전과 승인 유효성 / 쓰기: 업무·API 상태 전이
CREATE TABLE app.proposal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    forecast_id uuid NOT NULL,
    version integer NOT NULL CHECK (version>0),
    supersedes_id uuid,
    status text NOT NULL,
    content_hash app.sha256 NOT NULL,
    decision_as_of timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    next_demand_at timestamptz NOT NULL,
    blocking_codes text[] NOT NULL DEFAULT '{}',
    job_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, forecast_id, version),
    UNIQUE (scope_id, job_id),
    UNIQUE (scope_id, id, version, content_hash),
    CHECK (status IN ('pending','blocked','approved','rejected','expired','superseded')),
    CHECK (expires_at>decision_as_of),
    CHECK (expires_at<=next_demand_at)
);

-- 품목별 권고·재고·정책 근거 / 쓰기: 업무
CREATE TABLE app.proposal_line (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    proposal_id uuid NOT NULL,
    item_id uuid NOT NULL,
    policy_id uuid NOT NULL,
    inventory_version bigint NOT NULL,
    protection_end date NOT NULL,
    forecast_units app.quantity NOT NULL,
    safety_units app.quantity NOT NULL,
    available_units app.quantity NOT NULL,
    eligible_incoming app.quantity NOT NULL,
    reserved_order_units app.quantity NOT NULL,
    required_units app.quantity NOT NULL,
    packs integer NOT NULL CHECK (packs>=0),
    order_units app.quantity NOT NULL,
    unit text NOT NULL,
    basis jsonb NOT NULL,
    blocking_codes text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, proposal_id, item_id),
    UNIQUE (scope_id, id, proposal_id, item_id),
    UNIQUE (scope_id, id, item_id)
);

-- 주문안 버전·내용에 대한 불변 Yes/No / 쓰기: API
CREATE TABLE app.decision (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    proposal_id uuid NOT NULL,
    proposal_version integer NOT NULL,
    content_hash app.sha256 NOT NULL,
    choice text NOT NULL CHECK (choice IN ('approve','reject')),
    principal_id uuid NOT NULL,
    reason text,
    decided_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, proposal_id),
    UNIQUE (scope_id, id, choice)
);

-- 공급처별 주문 의도와 접수 상태 / 쓰기: API 생성·업무 전송
CREATE TABLE app.purchase_order (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    decision_id uuid NOT NULL,
    decision_choice text NOT NULL DEFAULT 'approve' CHECK (decision_choice='approve'),
    supplier_id uuid NOT NULL,
    order_key uuid NOT NULL,
    content_hash app.sha256 NOT NULL,
    status text NOT NULL,
    released_at timestamptz,
    released_by uuid,
    release_reason text,
    receipt_status text NOT NULL DEFAULT 'not_received',
    supplier_order_ref text,
    submit_fence uuid,
    submit_lease_until timestamptz,
    accepted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, decision_id, supplier_id),
    UNIQUE (scope_id, supplier_id, order_key),
    CHECK (status IN ('pending_send','sending','accepted','submission_unknown','rejected','blocked','voided')),
    CHECK (receipt_status IN ('not_received','partially_received','received'))
);

-- 승인한 수량과 품목의 발주 라인 / 쓰기: API 생성·업무 입고
CREATE TABLE app.purchase_order_line (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    order_id uuid NOT NULL,
    proposal_line_id uuid NOT NULL,
    item_id uuid NOT NULL,
    packs integer NOT NULL CHECK (packs>0),
    ordered_units app.quantity NOT NULL CHECK (ordered_units>0),
    received_units app.quantity NOT NULL DEFAULT 0,
    cancelled_units app.quantity NOT NULL DEFAULT 0,
    expected_at timestamptz NOT NULL,
    unit text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, proposal_line_id),
    UNIQUE (scope_id, order_id, item_id),
    UNIQUE (scope_id, id, item_id),
    UNIQUE (scope_id, id, order_id),
    CHECK (received_units+cancelled_units<=ordered_units)
);

-- 미전송·미확인 주문의 중복발주 예약 / 쓰기: API·업무
CREATE TABLE app.order_reservation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    order_line_id uuid NOT NULL,
    item_id uuid NOT NULL,
    remaining_units app.quantity NOT NULL,
    status text NOT NULL CHECK (status IN ('reserved','confirmed','released')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, order_line_id)
);

-- 전송·접수조회 시도와 불명 결과 / 쓰기: 업무
CREATE TABLE app.order_attempt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    order_id uuid NOT NULL,
    job_attempt_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('submit','lookup')),
    request_hash app.sha256 NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    outcome text NOT NULL CHECK (outcome IN ('in_flight','accepted','not_found','rejected','unknown')),
    supplier_ref text,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id)
);

-- 입고 통지와 정정 사건 / 쓰기: API 접수·업무 적용
CREATE TABLE app.receipt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    order_id uuid NOT NULL,
    source_receipt_key text NOT NULL,
    occurred_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('received','applied','rejected')),
    reverses_receipt_id uuid,
    body_hash app.sha256 NOT NULL,
    job_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, order_id, source_receipt_key),
    UNIQUE (scope_id, job_id),
    UNIQUE (scope_id, id, order_id)
);

-- 라인별 실제 입고량 / 쓰기: 업무
CREATE TABLE app.receipt_line (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    order_id uuid NOT NULL,
    order_line_id uuid NOT NULL,
    quantity app.quantity NOT NULL CHECK (quantity>0),
    unit text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, receipt_id, order_line_id)
);

-- 기술 로그와 분리한 업무 상태 이력 / 쓰기: 변경 주체
CREATE TABLE app.business_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    sequence bigint NOT NULL CHECK (sequence>0),
    kind text NOT NULL,
    principal_id uuid,
    job_id uuid,
    occurred_at timestamptz NOT NULL,
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, aggregate_type, aggregate_id, sequence)
);

-- 같은 PostgreSQL의 구조화 기술 로그 / 쓰기: 로거
CREATE TABLE app.app_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    event_id uuid NOT NULL,
    occurred_at timestamptz NOT NULL,
    level text NOT NULL CHECK (level IN ('DEBUG','INFO','WARNING','ERROR','CRITICAL')),
    service text NOT NULL,
    code text NOT NULL,
    message text NOT NULL,
    correlation_id uuid,
    job_id uuid,
    context jsonb NOT NULL DEFAULT '{}',
    payload_bytes integer NOT NULL CHECK (payload_bytes BETWEEN 0 AND 16384),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, event_id),
    CHECK (octet_length(message)+octet_length(context::text)<=16384)
);

-- 지연 도착 실제값으로 첫째·둘째 주 정확도 평가 / 쓰기: 업무 평가
CREATE TABLE app.forecast_evaluation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    forecast_id uuid NOT NULL,
    item_id uuid NOT NULL,
    evaluation_window text NOT NULL CHECK (evaluation_window IN ('week1','week2')),
    truth_hash app.sha256 NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','ready','unavailable')),
    observations integer NOT NULL CHECK (observations BETWEEN 0 AND 7),
    expected_labels integer NOT NULL DEFAULT 7 CHECK (expected_labels=7),
    mae app.quantity,
    bias app.finite_number,
    reason_code text,
    evaluated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, forecast_id, item_id, evaluation_window, truth_hash),
    CHECK ((status='ready' AND observations>0 AND mae IS NOT NULL AND bias IS NOT NULL) OR (status IN ('pending','unavailable') AND mae IS NULL AND bias IS NULL))
);

-- 평가 실제값의 제출 revision 추적 / 쓰기: 업무 평가
CREATE TABLE app.evaluation_delivery (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    evaluation_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, evaluation_id, delivery_id)
);

-- 새 scope 생성 전역 멱등성 기록 / 쓰기: API test_admin 전용
CREATE TABLE app.scenario_creation_request (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL,
    request_key text NOT NULL,
    request_hash app.sha256 NOT NULL,
    scope_id uuid NOT NULL,
    job_id uuid NOT NULL,
    http_status integer NOT NULL CHECK (http_status=202),
    response_body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal_id, request_key)
);

-- 과거 정정으로 영향을 받은 원점과 재제출 복구 / 쓰기: 수신
CREATE TABLE app.delivery_impact (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    correction_delivery_id uuid NOT NULL,
    affected_delivery_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('waiting_resubmission','resolved','historical_only')),
    resolution_delivery_id uuid,
    reason_code text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, correction_delivery_id, snapshot_id)
);

-- 가상 품목별 일별 요청·충족·부족 결과 / 쓰기: 시뮬레이터
CREATE TABLE app.demand_outcome (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id uuid NOT NULL,
    item_id uuid NOT NULL,
    day date NOT NULL,
    source_key text NOT NULL,
    occurred_at timestamptz NOT NULL,
    requested_units app.quantity NOT NULL,
    fulfilled_units app.quantity NOT NULL,
    lost_units app.quantity NOT NULL,
    unit text NOT NULL,
    inventory_version bigint NOT NULL CHECK (inventory_version>0),
    inventory_event_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scope_id, id),
    UNIQUE (scope_id, item_id, day),
    UNIQUE (scope_id, source_key),
    CHECK (requested_units=fulfilled_units+lost_units)
);

ALTER TABLE app.membership ADD CONSTRAINT fk_001 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.membership ADD CONSTRAINT fk_002 FOREIGN KEY (principal_id) REFERENCES app.principal (id);

ALTER TABLE app.artifact ADD CONSTRAINT fk_003 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.supplier ADD CONSTRAINT fk_004 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.item ADD CONSTRAINT fk_005 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.ordering_policy ADD CONSTRAINT fk_006 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.ordering_policy ADD CONSTRAINT fk_007 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.ordering_policy ADD CONSTRAINT fk_008 FOREIGN KEY (scope_id, supplier_id) REFERENCES app.supplier (scope_id, id);

ALTER TABLE app.model_version ADD CONSTRAINT fk_009 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.model_version ADD CONSTRAINT fk_010 FOREIGN KEY (scope_id, checkpoint_artifact_id) REFERENCES app.artifact (scope_id, id);

ALTER TABLE app.model_item ADD CONSTRAINT fk_011 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.model_item ADD CONSTRAINT fk_012 FOREIGN KEY (scope_id, model_version_id) REFERENCES app.model_version (scope_id, id);

ALTER TABLE app.model_item ADD CONSTRAINT fk_013 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.model_feature ADD CONSTRAINT fk_014 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.model_feature ADD CONSTRAINT fk_015 FOREIGN KEY (scope_id, model_version_id) REFERENCES app.model_version (scope_id, id);

ALTER TABLE app.delivery ADD CONSTRAINT fk_016 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.delivery ADD CONSTRAINT fk_017 FOREIGN KEY (scope_id, model_version_id) REFERENCES app.model_version (scope_id, id);

ALTER TABLE app.delivery ADD CONSTRAINT fk_018 FOREIGN KEY (scope_id, supersedes_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.delivery ADD CONSTRAINT fk_019 FOREIGN KEY (scope_id, artifact_id) REFERENCES app.artifact (scope_id, id);

ALTER TABLE app.delivery ADD CONSTRAINT fk_020 FOREIGN KEY (submitted_by) REFERENCES app.principal (id);

ALTER TABLE app.validation_issue ADD CONSTRAINT fk_021 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.validation_issue ADD CONSTRAINT fk_022 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.validation_issue ADD CONSTRAINT fk_023 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.usage_day ADD CONSTRAINT fk_024 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.usage_day ADD CONSTRAINT fk_025 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.usage_day ADD CONSTRAINT fk_026 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.patient_day ADD CONSTRAINT fk_027 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.patient_day ADD CONSTRAINT fk_028 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.inventory_anchor ADD CONSTRAINT fk_029 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.inventory_anchor ADD CONSTRAINT fk_030 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.inventory_anchor_line ADD CONSTRAINT fk_031 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.inventory_anchor_line ADD CONSTRAINT fk_032 FOREIGN KEY (scope_id, anchor_id) REFERENCES app.inventory_anchor (scope_id, id);

ALTER TABLE app.inventory_anchor_line ADD CONSTRAINT fk_033 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.external_open_order ADD CONSTRAINT fk_034 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.external_open_order ADD CONSTRAINT fk_035 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.external_open_order ADD CONSTRAINT fk_036 FOREIGN KEY (scope_id, supplier_id) REFERENCES app.supplier (scope_id, id);

ALTER TABLE app.external_open_order ADD CONSTRAINT fk_037 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.external_open_order ADD CONSTRAINT fk_038 FOREIGN KEY (scope_id, matched_order_line_id, item_id) REFERENCES app.purchase_order_line (scope_id, id, item_id);

ALTER TABLE app.input_snapshot ADD CONSTRAINT fk_039 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.input_snapshot ADD CONSTRAINT fk_040 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.input_snapshot ADD CONSTRAINT fk_041 FOREIGN KEY (scope_id, model_version_id) REFERENCES app.model_version (scope_id, id);

ALTER TABLE app.snapshot_source ADD CONSTRAINT fk_042 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.snapshot_source ADD CONSTRAINT fk_043 FOREIGN KEY (scope_id, snapshot_id) REFERENCES app.input_snapshot (scope_id, id);

ALTER TABLE app.snapshot_source ADD CONSTRAINT fk_044 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.snapshot_target ADD CONSTRAINT fk_045 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.snapshot_target ADD CONSTRAINT fk_046 FOREIGN KEY (scope_id, snapshot_id, model_version_id) REFERENCES app.input_snapshot (scope_id, id, model_version_id);

ALTER TABLE app.snapshot_target ADD CONSTRAINT fk_047 FOREIGN KEY (scope_id, model_version_id, item_id) REFERENCES app.model_item (scope_id, model_version_id, item_id);

ALTER TABLE app.snapshot_feature ADD CONSTRAINT fk_048 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.snapshot_feature ADD CONSTRAINT fk_049 FOREIGN KEY (scope_id, snapshot_id, model_version_id) REFERENCES app.input_snapshot (scope_id, id, model_version_id);

ALTER TABLE app.snapshot_feature ADD CONSTRAINT fk_050 FOREIGN KEY (scope_id, model_version_id, kind, feature_key) REFERENCES app.model_feature (scope_id, model_version_id, kind, feature_key);

ALTER TABLE app.job ADD CONSTRAINT fk_051 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.job ADD CONSTRAINT fk_052 FOREIGN KEY (scope_id, parent_job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.job_attempt ADD CONSTRAINT fk_053 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.job_attempt ADD CONSTRAINT fk_054 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.outbox ADD CONSTRAINT fk_055 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.outbox ADD CONSTRAINT fk_056 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.inbox ADD CONSTRAINT fk_057 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.inbox ADD CONSTRAINT fk_058 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.api_idempotency ADD CONSTRAINT fk_059 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.api_idempotency ADD CONSTRAINT fk_060 FOREIGN KEY (principal_id) REFERENCES app.principal (id);

ALTER TABLE app.forecast_run ADD CONSTRAINT fk_061 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.forecast_run ADD CONSTRAINT fk_062 FOREIGN KEY (scope_id, snapshot_id, model_version_id) REFERENCES app.input_snapshot (scope_id, id, model_version_id);

ALTER TABLE app.forecast_run ADD CONSTRAINT fk_063 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.forecast_value ADD CONSTRAINT fk_064 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.forecast_value ADD CONSTRAINT fk_065 FOREIGN KEY (scope_id, forecast_id, model_version_id) REFERENCES app.forecast_run (scope_id, id, model_version_id);

ALTER TABLE app.forecast_value ADD CONSTRAINT fk_066 FOREIGN KEY (scope_id, model_version_id, item_id) REFERENCES app.model_item (scope_id, model_version_id, item_id);

ALTER TABLE app.inventory_state ADD CONSTRAINT fk_067 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.inventory_state ADD CONSTRAINT fk_068 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.inventory_state ADD CONSTRAINT fk_069 FOREIGN KEY (scope_id, anchor_id) REFERENCES app.inventory_anchor (scope_id, id);

ALTER TABLE app.inventory_event ADD CONSTRAINT fk_070 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.inventory_event ADD CONSTRAINT fk_071 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.inventory_event ADD CONSTRAINT fk_072 FOREIGN KEY (scope_id, receipt_line_id) REFERENCES app.receipt_line (scope_id, id);

ALTER TABLE app.inventory_event ADD CONSTRAINT fk_073 FOREIGN KEY (scope_id, reverses_event_id) REFERENCES app.inventory_event (scope_id, id);

ALTER TABLE app.proposal ADD CONSTRAINT fk_074 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.proposal ADD CONSTRAINT fk_075 FOREIGN KEY (scope_id, forecast_id) REFERENCES app.forecast_run (scope_id, id);

ALTER TABLE app.proposal ADD CONSTRAINT fk_076 FOREIGN KEY (scope_id, supersedes_id) REFERENCES app.proposal (scope_id, id);

ALTER TABLE app.proposal ADD CONSTRAINT fk_077 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.proposal_line ADD CONSTRAINT fk_078 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.proposal_line ADD CONSTRAINT fk_079 FOREIGN KEY (scope_id, proposal_id) REFERENCES app.proposal (scope_id, id);

ALTER TABLE app.proposal_line ADD CONSTRAINT fk_080 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.proposal_line ADD CONSTRAINT fk_081 FOREIGN KEY (scope_id, policy_id, item_id) REFERENCES app.ordering_policy (scope_id, id, item_id);

ALTER TABLE app.decision ADD CONSTRAINT fk_082 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.decision ADD CONSTRAINT fk_083 FOREIGN KEY (scope_id, proposal_id, proposal_version, content_hash) REFERENCES app.proposal (scope_id, id, version, content_hash);

ALTER TABLE app.decision ADD CONSTRAINT fk_084 FOREIGN KEY (principal_id) REFERENCES app.principal (id);

ALTER TABLE app.purchase_order ADD CONSTRAINT fk_085 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.purchase_order ADD CONSTRAINT fk_086 FOREIGN KEY (scope_id, decision_id, decision_choice) REFERENCES app.decision (scope_id, id, choice);

ALTER TABLE app.purchase_order ADD CONSTRAINT fk_087 FOREIGN KEY (scope_id, supplier_id) REFERENCES app.supplier (scope_id, id);

ALTER TABLE app.purchase_order ADD CONSTRAINT fk_088 FOREIGN KEY (released_by) REFERENCES app.principal (id);

ALTER TABLE app.purchase_order_line ADD CONSTRAINT fk_089 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.purchase_order_line ADD CONSTRAINT fk_090 FOREIGN KEY (scope_id, order_id) REFERENCES app.purchase_order (scope_id, id);

ALTER TABLE app.purchase_order_line ADD CONSTRAINT fk_091 FOREIGN KEY (scope_id, proposal_line_id, item_id) REFERENCES app.proposal_line (scope_id, id, item_id);

ALTER TABLE app.purchase_order_line ADD CONSTRAINT fk_092 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.order_reservation ADD CONSTRAINT fk_093 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.order_reservation ADD CONSTRAINT fk_094 FOREIGN KEY (scope_id, order_line_id, item_id) REFERENCES app.purchase_order_line (scope_id, id, item_id);

ALTER TABLE app.order_reservation ADD CONSTRAINT fk_095 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.order_attempt ADD CONSTRAINT fk_096 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.order_attempt ADD CONSTRAINT fk_097 FOREIGN KEY (scope_id, order_id) REFERENCES app.purchase_order (scope_id, id);

ALTER TABLE app.order_attempt ADD CONSTRAINT fk_098 FOREIGN KEY (scope_id, job_attempt_id) REFERENCES app.job_attempt (scope_id, id);

ALTER TABLE app.receipt ADD CONSTRAINT fk_099 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.receipt ADD CONSTRAINT fk_100 FOREIGN KEY (scope_id, order_id) REFERENCES app.purchase_order (scope_id, id);

ALTER TABLE app.receipt ADD CONSTRAINT fk_101 FOREIGN KEY (scope_id, reverses_receipt_id) REFERENCES app.receipt (scope_id, id);

ALTER TABLE app.receipt ADD CONSTRAINT fk_102 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.receipt_line ADD CONSTRAINT fk_103 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.receipt_line ADD CONSTRAINT fk_104 FOREIGN KEY (scope_id, receipt_id, order_id) REFERENCES app.receipt (scope_id, id, order_id);

ALTER TABLE app.receipt_line ADD CONSTRAINT fk_105 FOREIGN KEY (scope_id, order_line_id, order_id) REFERENCES app.purchase_order_line (scope_id, id, order_id);

ALTER TABLE app.business_event ADD CONSTRAINT fk_106 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.business_event ADD CONSTRAINT fk_107 FOREIGN KEY (principal_id) REFERENCES app.principal (id);

ALTER TABLE app.business_event ADD CONSTRAINT fk_108 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.app_log ADD CONSTRAINT fk_109 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.forecast_evaluation ADD CONSTRAINT fk_110 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.forecast_evaluation ADD CONSTRAINT fk_111 FOREIGN KEY (scope_id, forecast_id) REFERENCES app.forecast_run (scope_id, id);

ALTER TABLE app.forecast_evaluation ADD CONSTRAINT fk_112 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.evaluation_delivery ADD CONSTRAINT fk_113 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.evaluation_delivery ADD CONSTRAINT fk_114 FOREIGN KEY (scope_id, evaluation_id) REFERENCES app.forecast_evaluation (scope_id, id);

ALTER TABLE app.evaluation_delivery ADD CONSTRAINT fk_115 FOREIGN KEY (scope_id, delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.scope ADD CONSTRAINT fk_116 FOREIGN KEY (id, model_version_id) REFERENCES app.model_version (scope_id, id);

ALTER TABLE app.scope ADD CONSTRAINT fk_117 FOREIGN KEY (id, active_job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.scenario_creation_request ADD CONSTRAINT fk_118 FOREIGN KEY (principal_id) REFERENCES app.principal (id);

ALTER TABLE app.scenario_creation_request ADD CONSTRAINT fk_119 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.scenario_creation_request ADD CONSTRAINT fk_120 FOREIGN KEY (scope_id, job_id) REFERENCES app.job (scope_id, id);

ALTER TABLE app.delivery_impact ADD CONSTRAINT fk_121 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.delivery_impact ADD CONSTRAINT fk_122 FOREIGN KEY (scope_id, correction_delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.delivery_impact ADD CONSTRAINT fk_123 FOREIGN KEY (scope_id, affected_delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.delivery_impact ADD CONSTRAINT fk_124 FOREIGN KEY (scope_id, snapshot_id) REFERENCES app.input_snapshot (scope_id, id);

ALTER TABLE app.delivery_impact ADD CONSTRAINT fk_125 FOREIGN KEY (scope_id, resolution_delivery_id) REFERENCES app.delivery (scope_id, id);

ALTER TABLE app.demand_outcome ADD CONSTRAINT fk_126 FOREIGN KEY (scope_id) REFERENCES app.scope (id);

ALTER TABLE app.demand_outcome ADD CONSTRAINT fk_127 FOREIGN KEY (scope_id, item_id) REFERENCES app.item (scope_id, id);

ALTER TABLE app.demand_outcome ADD CONSTRAINT fk_128 FOREIGN KEY (scope_id, inventory_event_id) REFERENCES app.inventory_event (scope_id, id);

CREATE UNIQUE INDEX uq_current_delivery ON app.delivery(scope_id,week_start,week_end_exclusive) WHERE status='validated';

CREATE UNIQUE INDEX uq_open_proposal ON app.proposal(scope_id,forecast_id) WHERE status IN ('pending','blocked');

CREATE INDEX ix_outbox_pending ON app.outbox(available_at,created_at) WHERE status IN ('pending','leased');

CREATE INDEX ix_job_recovery ON app.job(scope_id,status,updated_at);

CREATE INDEX ix_lease ON app.job_attempt(lease_until) WHERE status='running';

CREATE INDEX ix_order_unknown ON app.purchase_order(scope_id,status) WHERE status IN ('sending','submission_unknown');

CREATE UNIQUE INDEX uq_one_reversal ON app.receipt(scope_id,reverses_receipt_id) WHERE reverses_receipt_id IS NOT NULL;

CREATE INDEX ix_log_retention ON app.app_log(created_at);

CREATE INDEX ix_log_job ON app.app_log(scope_id,job_id,occurred_at);

CREATE INDEX ix_business_history ON app.business_event(scope_id,aggregate_type,aggregate_id,sequence);

ALTER TABLE app.membership ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.membership FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.membership USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.artifact ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.artifact FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.artifact USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.supplier ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.supplier FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.supplier USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.item ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.item FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.item USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.ordering_policy ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.ordering_policy FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.ordering_policy USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.model_version ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.model_version FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.model_version USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.model_item ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.model_item FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.model_item USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.model_feature ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.model_feature FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.model_feature USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.delivery ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.delivery FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.delivery USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.validation_issue ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.validation_issue FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.validation_issue USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.usage_day ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.usage_day FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.usage_day USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.patient_day ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.patient_day FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.patient_day USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.inventory_anchor ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.inventory_anchor FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.inventory_anchor USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.inventory_anchor_line ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.inventory_anchor_line FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.inventory_anchor_line USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.external_open_order ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.external_open_order FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.external_open_order USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.input_snapshot ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.input_snapshot FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.input_snapshot USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.snapshot_source ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.snapshot_source FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.snapshot_source USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.snapshot_target ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.snapshot_target FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.snapshot_target USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.snapshot_feature ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.snapshot_feature FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.snapshot_feature USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.job ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.job FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.job USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.job_attempt ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.job_attempt FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.job_attempt USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.outbox ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.outbox USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.inbox ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.inbox FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.inbox USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.api_idempotency ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.api_idempotency FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.api_idempotency USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.forecast_run ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.forecast_run FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.forecast_run USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.forecast_value ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.forecast_value FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.forecast_value USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.inventory_state ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.inventory_state FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.inventory_state USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.inventory_event ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.inventory_event FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.inventory_event USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.proposal ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.proposal FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.proposal USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.proposal_line ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.proposal_line FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.proposal_line USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.decision ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.decision FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.decision USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.purchase_order ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.purchase_order FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.purchase_order USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.purchase_order_line ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.purchase_order_line FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.purchase_order_line USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.order_reservation ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.order_reservation FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.order_reservation USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.order_attempt ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.order_attempt FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.order_attempt USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.receipt ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.receipt FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.receipt USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.receipt_line ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.receipt_line FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.receipt_line USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.business_event ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.business_event FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.business_event USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.app_log ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.app_log FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.app_log USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.forecast_evaluation ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.forecast_evaluation FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.forecast_evaluation USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.evaluation_delivery ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.evaluation_delivery FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.evaluation_delivery USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.delivery_impact ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.delivery_impact FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.delivery_impact USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.demand_outcome ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.demand_outcome FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.demand_outcome USING (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid) WITH CHECK (scope_id = nullif(current_setting('app.scope_id', true),'')::uuid);

ALTER TABLE app.scope ENABLE ROW LEVEL SECURITY;

ALTER TABLE app.scope FORCE ROW LEVEL SECURITY;

CREATE POLICY scope_isolation ON app.scope USING (id = nullif(current_setting('app.scope_id',true),'')::uuid) WITH CHECK (id = nullif(current_setting('app.scope_id',true),'')::uuid);

CREATE FUNCTION app.guard_snapshot_child() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid uuid; state text;
BEGIN
  sid := CASE WHEN TG_OP='DELETE' THEN OLD.snapshot_id ELSE NEW.snapshot_id END;
  SELECT status INTO state FROM app.input_snapshot WHERE id=sid FOR UPDATE;
  IF state IS DISTINCT FROM 'building' THEN RAISE EXCEPTION 'snapshot is not building'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  IF TG_OP='UPDATE' AND (OLD.snapshot_id<>NEW.snapshot_id OR OLD.scope_id<>NEW.scope_id) THEN RAISE EXCEPTION 'snapshot identity is immutable'; END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER sealed_input_guard BEFORE INSERT OR UPDATE OR DELETE ON app.snapshot_target FOR EACH ROW EXECUTE FUNCTION app.guard_snapshot_child();

CREATE TRIGGER sealed_input_guard BEFORE INSERT OR UPDATE OR DELETE ON app.snapshot_feature FOR EACH ROW EXECUTE FUNCTION app.guard_snapshot_child();

CREATE TRIGGER sealed_input_guard BEFORE INSERT OR UPDATE OR DELETE ON app.snapshot_source FOR EACH ROW EXECUTE FUNCTION app.guard_snapshot_child();

CREATE FUNCTION app.guard_snapshot_parent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status<>'building' THEN RAISE EXCEPTION 'sealed or invalid snapshot is immutable'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER sealed_parent_guard BEFORE UPDATE OR DELETE ON app.input_snapshot FOR EACH ROW EXECUTE FUNCTION app.guard_snapshot_parent();

COMMIT;
