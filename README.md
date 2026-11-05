# 천안의료원 약품·물품 수량 예측과 공급 효용성 연구

현재 서비스 화면은 **MTECH Demo v0.1**로 확정했습니다. 웹·경량 API 두 컨테이너에서 기존 예측을 재생하며, 시드는 사설 JSON에, 설정·진행 상태·이력은 SQLite 영구 볼륨에 저장합니다. [데모 실행 안내](deploy/demo/README.md) · [확정 범위](architect/13_PORTABLE_DEMO.md). 병원 자료·시드·DB·접속 정보·서버 이전 묶음은 저장소에 포함하지 않습니다.

## 데모 실행에 필요한 파일과 프로그램

아래 실행 방법은 **완성된 데모 묶음 `bundle.tar.gz`를 전달받은 사용자**를 기준으로 합니다. Windows에서도 화면과 API는 Docker의 Linux 컨테이너로 실행됩니다.

| 항목 | 필요 여부 |
|---|---|
| 비공개 데모 묶음 `bundle.tar.gz` | 필수. 기존 예측·집계 시드와 빌드된 화면 포함 |
| Docker 및 Docker Compose v2 이상 | 필수 |
| Python 3.11 이상 | 필수. 최초 파일 검증과 접속 토큰 생성에 사용 |
| 인터넷 연결 | 최초 이미지·Python 의존성 다운로드에 필요 |
| Node.js·npm·Git | 완성된 묶음 실행에는 불필요 |
| TimesFM·PyTorch·GPU·PostgreSQL | 이 데모 실행에는 불필요 |

**이 공개 저장소를 Git clone하는 것만으로는 병원 자료 데모를 실행할 수 없습니다.** 병원 집계·예측 시드, 접속 토큰, SQLite DB와 완성된 묶음은 Git에 포함하지 않습니다. 별도로 전달받은 묶음을 사용하십시오. 저장소의 `deploy/demo/compose.yaml`을 소스 폴더에서 바로 실행하는 방식도 아닙니다. 묶음 생성은 아래 [개발자가 데모 묶음을 다시 만드는 방법](#개발자가-데모-묶음을-다시-만드는-방법)을 따릅니다.

압축을 풀면 다음 구조가 나와야 합니다. 아래 안내는 압축 내부 최상위 폴더가 `bundle`인 경우입니다.

```text
bundle/
├── compose.yaml
├── Dockerfile
├── requirements.txt
├── config.json
├── manifest.json
├── prepare.py
├── private/
│   └── seed.json
├── frontend/dist/          # 이미 빌드된 화면
├── src/demo/               # 경량 API
└── deploy/                # 웹 서버 설정
```

`private/access-token`은 처음에는 없어도 정상입니다. `prepare.py`가 새로 생성합니다. 압축 파일 이름이 `bundle.tar.gz`와 다르면 아래 명령의 파일 이름을 실제 받은 이름으로 바꾸십시오.

## macOS·Linux 실행 방법

### 1. 실행 프로그램 설치

**macOS**

1. [Docker Desktop for Mac 공식 안내](https://docs.docker.com/desktop/setup/install/mac-install/)에서 Apple Silicon 또는 Intel에 맞는 설치 파일을 받습니다.
2. Docker Desktop을 설치하고 실행하여 엔진이 준비될 때까지 기다립니다.
3. [Python 공식 다운로드](https://www.python.org/downloads/macos/)에서 Python 3.11 이상을 설치합니다. 이미 설치되어 있다면 다시 설치하지 않아도 됩니다.
4. 터미널을 새로 열어 아래 명령을 확인합니다.

```sh
python3 --version
docker --version
docker compose version
docker info
```

`docker info`가 서버 정보를 반환해야 합니다. Docker 프로그램 설치만 하고 실행하지 않은 상태에서는 진행할 수 없습니다.

**Linux**

1. 배포판에 맞게 [Docker Engine](https://docs.docker.com/engine/install/)과 [Compose 플러그인](https://docs.docker.com/compose/install/linux/)을 설치합니다. Ubuntu는 [Docker의 Ubuntu 설치 안내](https://docs.docker.com/engine/install/ubuntu/)를 따릅니다.
2. Python 3.11 이상이 있는지 `python3 --version`으로 확인합니다.
3. systemd를 사용하는 일반적인 Linux 서버에서는 다음 명령으로 Docker 서비스를 시작합니다.

```sh
sudo systemctl enable --now docker
python3 --version
docker compose version
docker info
```

Docker 소켓에 대한 `permission denied`가 나오면 아래 Docker 명령을 `sudo docker ...`로 실행하거나, 관리자가 설정한 Docker 접근 권한을 사용하십시오. 예를 들어 `sudo docker compose up -d --build`입니다. Python 파일 준비 명령까지 sudo로 실행할 필요는 없습니다.

### 2. 데모 묶음 압축 해제

받은 파일이 `~/Downloads/bundle.tar.gz`에 있다고 가정합니다. Linux 서버에서는 실제 업로드한 파일 경로로 바꾸십시오. 기존 데모가 있는 폴더에 덮어쓰지 말고 새 폴더에서 풉니다.

```sh
mkdir -p "$HOME/mtech-demo"
cd "$HOME/mtech-demo"
tar -xzf "$HOME/Downloads/bundle.tar.gz"
cd bundle
ls
```

현재 폴더에 `compose.yaml`, `prepare.py`, `manifest.json`, `private/seed.json`이 있어야 합니다.

### 3. 최초 설정과 파일 검증

필요하면 **첫 실행 전에** `config.json`을 편집합니다. 기본값 그대로 시작해도 됩니다.

```json
{
  "hospital_name": "천안의료원",
  "start_date": "2024-03-04",
  "forecast_loading_seconds": 15
}
```

- `start_date`: 저장된 예측이 있는 시작 날짜입니다. 오늘 날짜를 임의로 넣지 않습니다.
- `forecast_loading_seconds`: 예측 결과를 받는 것처럼 기다리는 시간이며 0–600초 정수입니다.
- 파일 설정은 SQLite 상태가 없는 **최초 기동에만** 적용됩니다. 실행 후 변경은 화면의 Demo Setting을 사용합니다.

```sh
python3 prepare.py
docker compose config --quiet
```

첫 명령은 파일 해시를 검증하고 `private/access-token`을 생성합니다. 두 번째 명령은 Compose 설정이 유효하면 출력 없이 끝납니다.

### 4. 실행과 접속

```sh
docker compose up -d --build
docker compose ps
```

첫 실행에는 이미지 다운로드와 API 이미지 빌드로 시간이 걸립니다. `api`가 `healthy`, `web`이 실행 상태가 되면 브라우저에서 [http://127.0.0.1:8090](http://127.0.0.1:8090/)을 엽니다. 바로 열리지 않으면 잠시 후 `docker compose ps`를 다시 확인하십시오.

접속 화면의 **토큰 파일 선택**에서 방금 압축을 푼 폴더의 `private/access-token`을 선택하고 **연결**을 누릅니다. macOS 파일 선택 창에서 경로 입력이 필요하면 `Command+Shift+G`를 누르고 `~/mtech-demo/bundle/private`로 이동할 수 있습니다.

API 상태 확인:

```sh
curl -fsS http://127.0.0.1:8090/api/v1/health/live
```

정상이면 `{"status":"ok"}`가 나옵니다.

### 5. 종료와 다시 실행

항상 압축을 푼 `bundle` 폴더에서 실행합니다.

```sh
# 중지: 설정과 이력 보존
docker compose stop

# 다시 실행: 최초처럼 빌드할 필요 없음
docker compose start

# 오류 확인
docker compose logs --tail=100 api web
```

macOS에서는 먼저 Docker Desktop이 실행 중이어야 합니다. 터미널을 닫아도 `-d`로 실행한 컨테이너는 계속 동작합니다.

## Windows 실행 방법

아래 명령은 **Windows PowerShell** 기준입니다. Git Bash나 Ubuntu 터미널의 명령과 섞어서 실행하지 마십시오. 실행 파일은 OneDrive 동기화 폴더나 네트워크 드라이브보다 사용자 계정의 로컬 폴더에 두는 편이 관리하기 쉽습니다.

### 1. WSL 2 준비

Docker Desktop이 지원하는 Windows 환경을 준비합니다. 설치 전 현재 [Docker Desktop for Windows 요구사항](https://docs.docker.com/desktop/setup/install/windows-install/)을 확인하십시오. 이 데모는 **Linux 컨테이너 모드**를 사용하며 Windows 컨테이너 모드로 실행하지 않습니다.

WSL이 없다면 시작 메뉴에서 PowerShell을 찾아 **관리자 권한으로 실행**한 뒤 다음 명령을 실행하고 컴퓨터를 재시작합니다. Microsoft의 [WSL 설치 안내](https://learn.microsoft.com/en-us/windows/wsl/install)도 참고하십시오.

```powershell
wsl --install
```

이미 설치되어 있다면 설치를 반복하지 말고 버전과 상태를 확인합니다. 필요하면 업데이트합니다.

```powershell
wsl --version
wsl --status
wsl --update
```

WSL 설치 과정에서 Ubuntu 계정 생성을 요청하면 화면 안내를 완료합니다. 아래 데모 명령은 Ubuntu 안으로 들어가지 않고 Windows PowerShell에서 실행합니다. 가상화 관련 오류가 발생하면 Windows 및 BIOS/UEFI의 가상화 설정을 공식 설치 안내에 따라 확인해야 합니다.

### 2. Docker Desktop과 Python 설치

1. [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)를 설치하고 실행합니다.
2. Docker Desktop의 **Settings → General → Use the WSL 2 based engine**을 확인합니다. 설치 방식에 따라 해당 설정이 이미 기본 적용되어 있을 수 있습니다.
3. Windows 컨테이너 모드라면 Docker Desktop 메뉴에서 **Switch to Linux containers**로 전환합니다.
4. [Python for Windows](https://www.python.org/downloads/windows/)에서 Python 3.11 이상을 설치합니다. 설치 후 PowerShell을 새로 엽니다.
5. 다음 명령으로 준비 상태를 확인합니다. WSL 설치·업데이트처럼 관리자 권한을 요구하는 절차를 제외하면 일반 PowerShell에서 진행하면 됩니다.

```powershell
py --version
docker --version
docker compose version
docker info
```

`py`가 없고 `python --version`이 정상적으로 Python 3.11 이상을 반환하면 아래 `py prepare.py`를 `python prepare.py`로 바꿔 실행할 수 있습니다. `python`을 입력했을 때 Microsoft Store만 열리면 Python 설치와 실행 경로를 먼저 확인하십시오.

### 3. 데모 묶음 압축 해제

다운로드 폴더에 `bundle.tar.gz`가 있다고 가정합니다. 다음 명령은 `C:\Users\사용자명\mtech-demo\bundle`에 압축을 풉니다. 기존 폴더에 다른 데모가 있다면 새 이름의 폴더를 선택하십시오.

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\mtech-demo" | Out-Null
Set-Location "$env:USERPROFILE\mtech-demo"
tar -xzf "$env:USERPROFILE\Downloads\bundle.tar.gz"
Set-Location .\bundle
Get-ChildItem
Test-Path .\private\seed.json
```

마지막 명령은 `True`여야 합니다. `tar`가 없으면 `.tar.gz`를 풀 수 있는 압축 프로그램을 사용해도 됩니다. 압축을 두 단계로 풀었다면 최종적으로 `compose.yaml`과 `prepare.py`가 있는 폴더까지 들어와야 합니다.

### 4. 설정과 최초 실행

기본 설정은 시작 날짜 `2024-03-04`, 예측 로딩 시간 15초입니다. 바꾸려면 최초 기동 전에 `config.json`을 편집하십시오.

```powershell
notepad .\config.json
```

파일을 **UTF-8**로 저장하고 메모장을 닫은 뒤 실행합니다. 기본값을 쓰려면 편집을 생략합니다. 시드·소스·`manifest.json`은 편집하지 않습니다.

```powershell
py prepare.py
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

`api`가 `healthy`, `web`이 실행 상태가 될 때까지 기다립니다. 이미지 다운로드와 빌드에는 최초 한 번 인터넷이 필요합니다. 모델 파일을 내려받거나 GPU를 설정하는 과정은 없습니다.

### 5. 브라우저 접속과 로그인

```powershell
Start-Process "http://127.0.0.1:8090"
```

1. 접속 화면에서 **토큰 파일 선택**을 누릅니다.
2. `C:\Users\사용자명\mtech-demo\bundle\private` 폴더로 이동합니다.
3. 확장자가 없는 **`access-token`** 파일을 선택합니다.
4. **연결**을 누릅니다.

이 파일을 `access-token.txt`로 바꾸지 마십시오. 브라우저를 새로고침하면 토큰을 다시 선택해야 합니다. 토큰은 같은 묶음에서 재사용할 수 있으며 `prepare.py`를 다시 실행해도 기존 파일을 덮어쓰지 않습니다.

PowerShell에서 API 정상 여부를 확인할 수도 있습니다.

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8090/api/v1/health/live"
```

응답의 `status`가 `ok`이면 정상입니다.

### 6. 종료·재실행·오류 확인

```powershell
Set-Location "$env:USERPROFILE\mtech-demo\bundle"

# 중지: 설정과 이력 보존
docker compose stop

# 다시 실행: Docker Desktop을 먼저 켜 둡니다.
docker compose start

# 상태와 최근 오류 확인
docker compose ps
docker compose logs --tail=100 api web
```

창을 닫는 것과 데모를 중지하는 것은 다릅니다. `docker compose stop`을 실행하지 않으면 Docker가 켜져 있는 동안 데모가 계속 실행됩니다.

## 실행 후 공통 설정과 데이터 보존

화면의 **업무 설정 → Demo Setting**에서 시작 날짜와 예측 로딩 시간을 바꿀 수 있습니다. 적용하면 새 업무가 열리며 이전 이력은 보존됩니다. 시작 화면의 기존 예측은 즉시 표시하고, 이후 **주간 자료 전달**부터 설정한 대기 시간을 적용합니다.

| 데이터 | 저장 위치 | 보존 방식 |
|---|---|---|
| 품목·사용/출고량·환자 집계·저장된 예측 | `private/seed.json` | 읽기 전용 파일 |
| 설정·주차·작업 상태·요청 중복 방지·이력 | 컨테이너 `/state/demo.sqlite3` | Docker `mtech-demo_state` 볼륨 |
| 접속 토큰 | `private/access-token` | 준비할 때 생성한 파일 |

- `stop`, `start`, `restart`, 볼륨을 유지한 컨테이너 재생성은 DB 이력을 유지합니다.
- **`docker compose down -v`는 SQLite 볼륨과 이력을 삭제합니다.** 일반적인 종료에 사용하지 마십시오.
- 새 컴퓨터에서 묶음을 처음 실행하면 새 DB가 생깁니다. 기존 컴퓨터의 진행 이력까지 압축 파일에 포함된 것은 아닙니다.
- Compose 프로젝트 이름이 `mtech-demo`로 고정되어 있으므로 같은 Docker 환경의 여러 묶음은 기본적으로 같은 볼륨을 참조합니다. 다른 시드의 별도 데모는 `docker compose -p 다른이름 ...`으로 프로젝트를 분리해야 하며, 그 경우 이후 명령에도 같은 `-p`를 사용합니다.

### 포트 변경과 다른 기기에서 접속

기본 주소는 실행한 컴퓨터 안에서만 접근하는 `127.0.0.1:8090`입니다. 다른 컴퓨터에서 접속할 때 `127.0.0.1`을 쓰면 그 다른 컴퓨터 자신을 가리킵니다.

8090 포트가 사용 중이면 `compose.yaml` 옆에 `.env` 파일을 만들고 다음 내용을 저장합니다. `.env.txt`가 아닌지 확인하십시오.

```dotenv
WEB_PORT=8091
```

그다음 `docker compose up -d`를 실행하고 `http://127.0.0.1:8091`로 접속합니다. macOS·Linux·Windows 모두 같습니다.

같은 네트워크의 다른 기기에도 열어야 한다면 `.env`를 다음처럼 설정합니다.

```dotenv
BIND_ADDRESS=0.0.0.0
WEB_PORT=8090
```

`docker compose up -d`로 반영한 뒤 `http://실행컴퓨터의IP:8090`으로 접속합니다. 운영체제 방화벽에서도 필요한 접근을 허용해야 합니다. 원격 Linux 서버를 프록시 뒤에서 제공한다면 기본 loopback 바인딩을 유지하고 프록시를 통해 연결할 수 있습니다.

### 자주 발생하는 문제

| 현상 | 확인·조치 |
|---|---|
| Docker daemon에 연결할 수 없음 | Docker Desktop 또는 Linux Docker 서비스를 켜고 `docker info` 확인 |
| `no configuration file provided` | 저장소 루트가 아니라 압축 해제한 `bundle` 폴더인지 확인 |
| `private/seed.json`이 없거나 API가 시작되지 않음 | 소스만 받은 것은 아닌지, 완성 묶음 압축이 모두 풀렸는지 확인 |
| `BUNDLE_HASH_MISMATCH` | 시드·소스·정적 파일이 바뀌었거나 손상됨. 원본 묶음을 새 폴더에 다시 풀기. 해시 검사를 끄지 않기 |
| Windows에서 해시 오류가 발생함 | Git checkout의 줄바꿈 변환을 거친 파일과 섞지 말고, 받은 `.tar.gz`를 그대로 압축 해제 |
| `port is already allocated` | 위 `.env` 방법으로 `WEB_PORT` 변경 |
| 화면은 열리지만 접속 토큰이 거부됨 | 같은 묶음의 `private/access-token`인지 확인. 기존에 다른 경로로 실행한 API가 있는지 `docker compose ps` 확인 |
| Windows에서 WSL·가상화 오류 | 공식 WSL/Docker 요구사항, 업데이트와 재부팅, Linux 컨테이너 모드 확인 |
| Mac에서 bind mount/파일 접근 오류 | Docker Desktop의 파일 공유 접근이 허용된 로컬 폴더인지 확인 |
| 설정 파일을 바꿨는데 시작 날짜가 그대로임 | 기존 SQLite 상태가 있음. 실행 후 변경은 화면의 Demo Setting 사용 |

## 개발자가 데모 묶음을 다시 만드는 방법

실행용 PC에 필요한 절차가 아닙니다. **사설 집계 JSON과 기존 예측 Parquet가 있는 개발 환경**에서만 수행합니다. 아래 예시는 macOS·Linux 셸 기준입니다. Windows 실행 사용자에게는 생성된 `bundle.tar.gz`를 별도로 전달합니다.

1. 이 저장소를 내려받고 Node.js 22·npm, Python 개발 환경을 준비합니다.
2. 사설 집계 JSON과 약품·물품의 기존 `*_joint_evaluation.parquet`를 로컬에 준비합니다. Git에는 추가하지 않습니다.
3. 프론트 의존성과 묶음 생성에 필요한 최소 Python 의존성을 설치합니다. 루트 연구 환경 전체를 설치할 필요는 없습니다.

```sh
git clone https://github.com/SKKUGoon/thesis-TBD.git
cd thesis-TBD
npm --prefix frontend ci
python3 -m venv .venv
.venv/bin/python -m pip install pandas pyarrow
```

4. 다음 명령에서 입력 경로를 실제 사설 파일 위치로 바꿉니다. 출력 폴더는 아직 없어야 합니다.

```sh
.venv/bin/python deploy/demo/build_bundle.py \
  --records /absolute/path/to/cheonan-records-v1.json \
  --forecast-run /absolute/path/to/timesfm_vector_20261008_01 \
  --output runs/cheonan/demo_export_01/bundle
```

`runs/cheonan/demo_export_01/bundle.tar.gz`가 생성됩니다. 묶음은 프론트 빌드·입력 대조·시드 검증·파일별 SHA-256 기록을 포함합니다. 이 명령으로 TimesFM을 새로 실행하지는 않습니다. 다른 PC에는 `.tar.gz`를 전달하고 위 운영체제별 실행 절차를 사용합니다.

## 실행 검증 범위

macOS의 Docker Desktop에서 Linux ARM64 컨테이너 빌드·기동, 실제 브라우저 화면, 설정·작업 보존과 재시작을 확인했습니다. **Windows와 별도 Linux 서버에서 이 안내를 직접 실행한 검증은 아직 하지 않았습니다.** 각 환경의 설치 절차는 Microsoft·Docker·Python 공식 안내를 참고했고, 명령의 파일·폴더 구성은 현재 묶음 생성 코드와 대조했습니다.

## 연구 개요와 기존 문서

현재 계획 범위는 과거 사용기록이 많은 약품 20개와 물품 약 10개의 별도 TimesFM 벡터 예측 및 같은 코호트의 부스팅·주문 효용성 비교입니다. 품목 선정에는 2020–2022년만 사용합니다. 2026-10-08 TimesFM의 공동/독립 예측과 주 조건 주문 정책 비교를 MPS로 완료했습니다. 새 코호트 부스팅 비교와 실제 운영 검증은 미완료입니다. 기존 뉴론텍·Forcep jar 결과는 예비 실험으로 보존합니다. 물품 목표는 기록 출고수량입니다.

약품 정보만으로 한 그룹을 고정하고 그 소모량을 ARIMA·VAR·XGBoost·TimesFM으로 예측·비교하는 연구입니다. 소비 패턴은 그룹 구성에 사용하지 않습니다. A→B→A 사용 상품 전환은 확인되지 않은 가능성으로 고려하고 그룹 총량과 개별 상품 배분을 구분합니다.

이번 목표는 전체 병원 물류 효율화로 가기 위한 첫 예측 연구입니다. 실제 재고·보충 정책·비용 절감 검증과 소모품 LLM 분류는 후속 범위입니다. 2026-10-07에 같은 제품의 병원 코드 두 개를 고정하여 ARIMA 탐색 실행을 완료했습니다. 무기록은 미관측으로 유지했으며, 이후 진단·입원/외래·연령 구성으로 XGBoost/Ridge 비교까지 실행했습니다. 전체 연구 비교와 실제 물류 개선 검증은 미완료입니다.

- [최신 TimesFM 30품목 통합 보고서](runs/cheonan/timesfm_vector_20261008_01/report.md)
- [기존 2품목 통합 물류 예측·주문 효율성 보고서](runs/cheonan/boosting_two_items_20261007_01/report.md)
- [부스팅·효용성 방법론](research/methods/BOOSTING.md)
- [최신 연구계획서 v1.14](research/RESEARCH_PROPOSAL.md)
- [TimesFM 주간 예측·가상 주문 시스템 설계와 장애·승인·로깅 정책](architect/README.md)
- [가상 물류 시스템 실행·테스트·서버 이전 안내](deploy/README.md)
- [현재 MTeCH 실제 자료·예측 비교 화면](architect/11_RECORDED_DATA_AND_FORECAST_HISTORY.md)
- [MTeCH 프론트엔드 API 연결과 브라우저 검증](architect/10_FRONTEND_IMPLEMENTATION.md)
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

현재 운영 UI는 [최신 대시보드 구현](architect/12_DASHBOARD_IMPLEMENTATION.md)을 따릅니다. 두 번째 대시보드를 유일한 UI로 확정했으며 이전 UI와 목업 진입점은 삭제했습니다.

데모의 **TimesFM ↔ Naive** 전환은 [데모 기능 설정](deploy/demo/README.md#데모-전용-기능과-모델-전환)을 따릅니다. 묶음 폴더 `.env`의 `DEMO_MODE=false`로 전환 버튼과 Demo Setting을 끌 수 있습니다. 변경 후 `docker compose up -d`를 실행하고 브라우저를 새로고침하십시오.
