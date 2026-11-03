모델 없이 기존 예측을 재생하는 경량 서버 데모는 [별도 배포 안내](demo/README.md)와 [13단계 설계](../architect/13_PORTABLE_DEMO.md)를 따릅니다. 아래 Compose는 기존 실제 TimesFM 평가 스택입니다.

현재 기본 화면은 실제 품목·사용 기록을 연결한 [확정 대시보드](../architect/12_DASHBOARD_IMPLEMENTATION.md)을 따른다. 설치 후 사설 집계 자료를 등록해야 하며, 실재고·발주 조건은 미등록으로 구분한다. 아래 이전 생성 자료의 주문·입고 절차를 실제 병원 운영 결과로 해석하지 않는다.

# 가상 물류 평가 시스템 실행

실제 TimesFM 고정 가중치를 사용하는 Linux CPU 평가 시스템입니다. DB·브로커·API·수신·TimesFM·업무·Outbox 복구·로거·가상 공급처를 Compose로 실행합니다. 확정한 MTeCH 화면을 웹 컨테이너에서 제공하며 동일 주소의 `/api/` 프록시로 연결합니다.

검증 범위와 결과는 [구현 기록](../architect/08_IMPLEMENTATION.md)을 따릅니다. 실측 환경은 Linux ARM64 CPU이며 AMD64·CUDA·실제 병원 운영 검증은 포함하지 않습니다. 가상 시나리오는 공식 달력 참조가 준비된 2020–2024년 범위에서 실행합니다.

## 최초 설치

호스트에 Docker Engine/Desktop·Compose v2와 Python 3.11 이상이 필요합니다. 실행 컨테이너는 Python 3.12입니다. 아래 명령은 저장소 루트에서 실행합니다.

```sh
python3 deploy/prepare.py --checkpoint /absolute/path/to/checkpoint
docker build -f deploy/Dockerfile.timesfm -t paper-timesfm:research-v1 .
docker compose -f deploy/compose.yaml build api timesfm web
python3 deploy/prepare.py --checkpoint deploy/.state/model
docker compose -f deploy/compose.yaml up -d --no-build
python3 deploy/mint_token.py
```

체크포인트 revision은 `43046b85ec22d584a13f8098c2ed39c889e129c2`입니다. 준비 명령은 `config.json`과 `model.safetensors`의 SHA-256을 검증하고 사설 모델 디렉터리에 복사합니다. 모델을 새로 내려받는 기능은 넣지 않았습니다. 서비스 실행 시 네트워크 다운로드는 비활성화합니다. 두 번째 준비 명령은 빌드된 ML 이미지 digest와 소스 해시를 등록하므로 생략하지 않습니다.

프론트엔드는 <http://127.0.0.1:8090/>입니다. 접속 화면에서 **토큰 파일 선택**으로 `deploy/.state/test-token`을 열고 `연결`을 누릅니다. 토큰 본문은 브라우저 메모리에만 유지하며 새로고침하면 다시 입력합니다. 만료 시 `python3 deploy/mint_token.py`로 재발급합니다.

화면에서는 좌측 환자·품목별 예측 그래프와 우측 발주표를 확인합니다. `주간 자료 전달`로 다음 마감 주간을 전달하고, 자료·처리 내역은 환자 카드의 메뉴에서 엽니다. 발주 검토·공급처 접수·입고는 각각 대시보드의 기능별 창을 사용합니다. 이전 5단계 UI와 독립 디자인 미리보기는 삭제되었습니다. 실제 재고·포장 단위·납기는 미등록이며 해당 조건을 등록하기 전에는 주문안을 만들지 않습니다.

웹 포트는 `WEB_PORT`로 변경할 수 있으며 기본값은 8090입니다. 프론트엔드는 DB·브로커에 직접 접근하지 않습니다. 현재 UI는 발급된 가상 운영 역할을 갖춘 주체를 대상으로 하며 실제 사용자 로그인·SSO는 별도입니다.

API 문서는 <http://127.0.0.1:8088/docs>입니다. Swagger의 Authorize에 `deploy/.state/test-token` 내용을 입력합니다. 시나리오 생성 외의 업무 API에는 생성 결과의 `scenario_id`를 `X-Workspace-ID`로 지정합니다. 변경 요청에는 `Idempotency-Key`를 사용합니다. 토큰 유효기간은 8시간이며 재발급할 수 있습니다.

웹·API는 기본 loopback 포트만 엽니다. DB·브로커·공급처의 호스트 포트는 열지 않습니다. API도 기본적으로 호스트의 loopback에만 엽니다. 모델은 읽기 전용이며 앱 컨테이너 파일시스템도 읽기 전용입니다. `deploy/.env`와 `.state/`는 Git·이미지·서버 내보내기에서 제외합니다. 비밀값을 명령 출력이나 문서에 붙이지 않습니다.

최초 DB 설치는 `schema.sql` → `init.sql` → 생성된 `roles.sql` 순서입니다. 기존 볼륨에 초기 SQL을 자동 재적용하지 않습니다. 이후 스키마 변경에는 별도의 명시적 migration이 필요합니다. 정상 중지는 다음 명령으로 수행하며 DB와 브로커 볼륨은 유지합니다.

```sh
docker compose -f deploy/compose.yaml stop
docker compose -f deploy/compose.yaml start
```

## 주간 시험

1. `POST /test/scenarios`: `timesfm30_v1`은 약품 20·물품 10, `smoke_v1`은 3·2품목입니다. 예: 첫 월요일 `2024-03-04`, 초기 이력 112일입니다.
2. 시나리오가 `ready`가 되면 `POST /test/scenarios/{id}/weekly-deliveries`를 호출합니다. `expected_version`과 `week_start`는 현재 시나리오 응답을 사용합니다.
3. 수신·봉인·실제 예측·주문안 계산이 완료될 때까지 작업 또는 시나리오를 조회합니다. 약품·물품은 별도 벡터로 다음 14일을 예측합니다.
4. 주문안의 `version/content_hash`로 `approve` 또는 `reject`를 명시합니다. Yes 없이 발주하지 않습니다.
5. 주문 접수 확인 후 입고 API로 부분·전체 입고를 시험하거나 다음 주 진행 시 예정 입고를 자동 반영합니다. 결과 불명 주문은 `reconcile`로 조회합니다.

다음 시험은 실제 모델을 사용하며 결과는 비공개 `runs/`에 남깁니다. 장애 시험은 브로커·로거·TimesFM을 중단하므로 이 가상 평가 스택에서만 실행합니다.

```sh
python3 tests/operations/system_scenario.py --output runs/cheonan/my_operations/system.json
python3 tests/operations/fault_scenario.py --output runs/cheonan/my_operations/faults.json
python3 tests/operations/restore_scenario.py --order-id ACCEPTED_ORDER_UUID --output runs/cheonan/my_operations/restore.json
```

`system_scenario.py`의 기본값은 6주이며 `--weeks 1`로 짧게 실행할 수 있습니다. 실행마다 새 scope를 만들고 기존 결과를 덮어쓰지 않습니다. 출력 파일 경로는 실행별로 구분합니다. `fault_scenario.py`는 lease 복구 시간을 줄이기 위해 폐기한 시도의 lease만 시험 DB에서 과거로 바꿉니다. 복원 시험은 새 임시 DB를 만들고 검사 후 그 DB만 제거하며 백업은 출력 디렉터리에 보존합니다.

## 프론트엔드 개발과 검사

Node.js 22와 npm이 있는 환경에서 다음 명령을 사용합니다. 실제 브라우저 검사는 위 Compose 스택과 유효한 토큰 파일이 필요하며 새 가상 업무를 생성합니다. 토큰이 포함될 수 있는 네트워크 trace·동영상은 저장하지 않습니다.

```sh
cd frontend
npm ci
npm run dev
npm run build
npm test
npm run test:e2e
```

개발 화면은 Vite가 출력한 주소이며 `/api`를 8088로 전달합니다. 배포 화면은 8090의 웹 컨테이너입니다. macOS 검사는 설치된 Chrome을 사용하고 다른 환경에서는 Playwright Chromium 또는 `CHROME_BIN`을 지정합니다. `WEB_BASE`로 검사 주소를 변경할 수 있습니다. 테스트 결과는 비공개 `runs/cheonan/frontend_20261022_01/`에 보존합니다. 서버 API의 추가 권한·목록 검사는 다음과 같습니다.

```sh
python3 tests/operations/frontend_access_checks.py --output runs/cheonan/my_operations/frontend_access.json
```

상세 구현과 검증 경계는 [프론트엔드 구현 기록](../architect/10_FRONTEND_IMPLEMENTATION.md)을 따릅니다.

## 서버 이전 패키지

```sh
python3 deploy/export_bundle.py --output runs/cheonan/my_operations/server_bundle
```

새 디렉터리에 웹 이미지를 포함한 Docker 이미지 아카이브, 프론트엔드 소스·고정 의존성, 코드·명세·실행 도구·검사 도구, 검증된 모델, 라이선스, 파일별 SHA-256을 모읍니다. 기존 DB·로그·병원 자료·자격정보는 포함하지 않습니다. 해당 디렉터리를 같은 CPU 구조의 서버로 옮긴 뒤 다음 명령을 실행합니다.

```sh
python3 deploy/start_bundle.py
```

파일 해시·서버 아키텍처를 검사하고 이미지를 가져온 뒤 새 자격정보로 새 평가 DB를 시작합니다. ARM64 묶음을 AMD64 검증 결과로 취급하지 않습니다. 다른 CPU 서버에서는 해당 구조로 이미지를 다시 빌드하고 Linux preflight부터 재검사합니다.

`requirements-service.lock`은 서비스 이미지의 전체 Python 의존성을 고정합니다. `requirements-timesfm.lock`은 실제 ML 이미지 환경의 기록입니다. 최초 모델 이미지의 재빌드에는 공식 Git commit과 직접 의존성 버전을 고정하며, 서버 이전은 검증된 이미지 digest와 파일 해시를 기준으로 합니다.
