# 3단계 메시지 전달과 작업 복구

설계 버전 0.1 · 2026-10-21 · 설계 당시 정책 · 현재 구현·검증은 8단계 참조

명령·이벤트·Outbox·확인·중복 처리·재시도·중단 작업 복구를 정의한다.

[설계 진입점](README.md) · [현재 구현과 검증](08_IMPLEMENTATION.md)

## 메시지와 Outbox 정책

### 명령과 이벤트 구분

명령은 실행할 책임 모듈 한 곳으로 보낸다. 같은 완료 이벤트를 여러 모듈이 모두 받아야 하면 구독자마다 큐를 둔다. 서로 다른 후속 기능을 하나의 큐에서 경쟁 소비하게 만들지 않는다.

| 구분 | 수신 큐 | 처리 역할 |
|---|---|---|
| `ingestion.validate` | `q.ingestion` | 수신·검증 |
| `scenario.bootstrap` | `q.ingestion` | 가상 초기 입력·품목·고정 모델 계약 준비 |
| `scenario.weekly_deliver` | `q.business` | 가상 일별 입고·수요·부족 처리 후 일반 주간 수신 경로 연결 |
| `forecast.generate` | `q.forecast` | TimesFM |
| `proposal.calculate`, `order.submit`, `order.reconcile`, `receipt.apply`, `forecast.evaluate` | `q.business` | 업무 |
| `*.completed`, `*.failed` | 필요한 구독자의 전용 큐 | 알림·관측의 보조 용도 |
| `log.record` | `q.logs` | 로거 |

다음 필수 업무 단계는 DB 트랜잭션에 다음 명령을 Outbox로 기록하여 연결한다. 완료 이벤트를 발행했다는 사실만으로 다음 단계가 실행되었다고 가정하지 않는다. 메시지 본문에는 자료 원문·배열·환자 행 대신 식별자·버전·해시 참조만 넣는다.

```json
{
  "message_id": "uuid",
  "schema_version": 1,
  "type": "forecast.generate",
  "job_id": "uuid",
  "attempt": 1,
  "correlation_id": "uuid",
  "causation_id": "previous-message-uuid",
  "hospital_id": "synthetic-hospital",
  "scenario_id": "uuid",
  "input_snapshot_id": "uuid",
  "model_version_id": "uuid",
  "forecast_origin": "2026-10-26",
  "horizon_days": 14
}
```

이 예시는 초기 메시지 계약이며 UUID는 구현 시 생성한다. 발행 확인이 불명확하여 같은 메시지를 다시 보내면 `message_id`를 유지한다. 실패 후 새 실행 시도를 예약하면 같은 `job_id`에 `attempt`를 증가시키고 새 `message_id`를 부여한다. 지원하지 않는 `schema_version`이나 형식 오류가 있는 메시지는 재시도하지 않고 실패 참조를 DB에 기록한 후 보류한다.

### DB 저장과 발행

1. 생산 모듈은 업무 변경·작업 상태·필요한 업무 이력·다음 Outbox를 동일 트랜잭션에 저장한다.
2. Outbox 전달자는 짧은 lease로 미전달 행을 확보하고 `message_id`를 유지하여 RabbitMQ에 발행한다. DB 잠금을 유지한 채 네트워크 확인을 기다리지 않는다.
3. 업무 명령은 영속화하고 publisher confirm과 mandatory 발행에 따른 라우팅 실패 검출을 사용한다. nack·시간 초과·수신 큐 없음에서는 전달 완료로 처리하지 않는다. 보조 완료 이벤트는 등록한 구독자가 있을 때만 Outbox를 만들고, 필수 후속 명령과 전달 상태를 구분한다.
4. 확인 후 Outbox를 전달 완료로 기록한다. 발행 확인과 DB 갱신 사이 종료되면 재발행될 수 있으므로 소비자가 중복을 흡수한다.
5. DB·브로커 장애 중에는 기존 Outbox를 보존한다. API는 원본 저장과 DB 접수 커밋이 성공한 경우에만 202를 반환하고, 발행 대기는 `dispatch_pending`으로 표시한다. DB 접수 실패를 성공으로 반환하지 않는다.

업무 데이터와 메시지 발행의 이중 쓰기 문제에 Outbox를 적용한다. [AWS 공식 설명](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) 발행 확인과 소비 완료 확인은 별개다. [RabbitMQ 공식 설명](https://www.rabbitmq.com/docs/confirms)

### 큐의 초기 설정

| 항목 | 가상 평가 기본값 |
|---|---|
| 업무 큐 | 단일 노드의 durable classic queue, 영속 메시지, 자동 만료 없음 |
| 업무 큐 상한 | 큐별 대기 1,000건 또는 본문 128 MiB 중 먼저 도달한 한도, overflow는 `reject-publish` |
| 모델 실행 | 동시성 1·prefetch 1 |
| 업무 수신 | 동시성 1로 시작, 짧은 DB 트랜잭션과 외부 I/O 분리 |
| 작업 확인 | 결과·처리 기록·다음Outbox를 DB에 확정한 뒤 manual ack |
| 미완료 메시지 | 무한 즉시 requeue 금지. DB 재시도 예약 또는 보류로 전환 |
| 보류 | DB에 원문 없는 실패 메타데이터·참조를 보존하고 수동 검토 대상으로 표시 |

영속 큐는 같은 노드의 정상 재시작 복구를 위한 선택이며, 서버·디스크 상실에 대한 복제나 무손실 보장이 아니다. 큐 상한은 대기 메시지에 대한 제한이므로 미확인 메시지도 prefetch로 제한한다. [RabbitMQ 큐 길이](https://www.rabbitmq.com/docs/maxlength)

## 작업 상태와 재시도 정책

작업 상태는 `queued → running → succeeded / retry_wait / failed / blocked`로 관리한다. 작업과 실행 시도는 구분한다. `succeeded`는 해당 단계의 결과가 DB에 확정되었다는 의미다. 예측 성공이 주문안 성공이나 주문 접수를 뜻하지 않는다.

| 설정 | 가상 평가 기본값 |
|---|---|
| 실행 시도 | 최초 포함 최대 4회 |
| 일시적 실행 오류 재시도 | 10초·60초·300초, 각 간격에 최대 20% 지터 |
| heartbeat / lease | 실제 시각 기준 30초 / 180초 |
| 추론 한도 | 30분, 실제 모델 검사 후 조정 |
| 브로커 ack timeout | 60분으로 명시 설정하고 선택 버전 지원 확인 |
| Outbox 확인 | 평시 2초, 장애 시 최대 60초까지 후퇴, 유실 없이 DB 대기 |
| 정체 탐지 | 마지막 heartbeat·lease·Outbox 대기시간을 DB 기준으로 확인 |
| 미실행 작업 확인 | 5분 이상 대기 중인 작업의 Outbox·수신 상태를 확인하고, 선점·재전달 기록 후 같은 유효 시도 재발행 |

각 시도는 선점 토큰을 갖는다. lease가 만료되어 새 시도가 시작되면 이전 시도의 결과 확정을 거부한다. 메시지가 중복 도착해 유효한 실행이 이미 있으면 새 추론을 시작하지 않는다. 이전 시도의 늦은 메시지도 시도 번호·토큰으로 거부한다. 완전히 같은 완료 작업은 기존 결과 참조를 유지하고 ack한다. 추론 자체가 중복 계산될 가능성과 DB 결과·외부 주문이 중복 반영되는 것은 구분한다.

오래된 실행의 복구는 DB 선점·토큰을 갱신하고 새 시도 메시지를 Outbox에 기록한다. ML이 DB를 감시하여 새 주간을 찾지는 않지만 전달·복구 실행기는 제한된 주기로 DB 작업 상태를 확인한다. 재시도 예약을 DB에 확정한 뒤 실패 시도의 메시지를 ack하며, 장시간 sleep이나 즉시 재큐잉으로 예약하지 않는다.

자료 형식·단위·미지원 모델·해시 불일치는 자동 재시도하지 않는다. DB·브로커 불가로 실행이 시작되지 못한 대기는 모델 실패 횟수와 구분한다. 공급처 응답 유실은 일반 작업 재시도 규칙으로 재주문하지 않고 [승인·주문 정책](04_FORECAST_AND_ORDER.md)의 접수 확인 규칙을 적용한다. [RabbitMQ 소비자 확인 시간](https://www.rabbitmq.com/docs/consumers)
