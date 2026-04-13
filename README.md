# eTL Calendar Sync

FastAPI 앱. 로컬 실행: `uvicorn app.main:app` (엔트리 `app/main.py`).

## 프로덕션 실행 예 (호스팅)

플랫폼이 할당한 포트를 쓰려면 `0.0.0.0`과 `$PORT`를 사용합니다.

```bash
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
```

- **Render / Railway / Fly**: 대시보드에서 `PORT`가 주입되는 경우가 많습니다. 위처럼 `${PORT:-8000}`으로 두면 로컬에서는 8000 기본값을 쓸 수 있습니다.

## Render Web Service (Blueprint)

루트의 [`render.yaml`](render.yaml)은 **예시 Blueprint**입니다. Blueprint의 `name`은 기본 퍼블릭 URL 후보(`https://<name>.onrender.com`)에 쓰이는 경우가 많지만, **최종 호스트·HTTPS URL은 Render 대시보드의 Public URL만** 따릅니다. 배포 후 그 URL의 **호스트 부분**을 아래에서 `<실제호스트>`로 두고, `GOOGLE_REDIRECT_URI`·Google Console·JSON `redirect_uris`를 한꺼번에 맞춥니다.

### Build / Start (대시보드에 직접 넣을 때도 동일)

| 항목 | 값 |
|------|-----|
| **Root Directory** | `.` (레포 루트) |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

Render는 `PORT`를 자동 주입합니다. 로컬에서는 `PORT` 없이도 위 «프로덕션 실행 예»처럼 `${PORT:-8000}`을 쓰면 됩니다.

### 환경 변수 (`app/config.py` 기준)

| 변수 | 필수 | 설명 |
|------|------|------|
| `APP_SECRET_KEY` | **예** | JWT·세션 등 서명용 비밀값 |
| `CRYPTO_KEY` | **예** | Fernet 키(민감 필드 암호화). 생성: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_URL` | 아니오 (기본 `sqlite:///./data.db`) | SQLAlchemy URL. 아래 «프로덕션 SQLite» 참고 |
| `GOOGLE_CREDENTIALS_JSON` | **Render 권장** | Google OAuth **클라이언트 JSON 전체**를 문자열로(`json.dumps` 한 줄). 설정 시 파일 없이 동작 |
| `GOOGLE_CLIENT_SECRETS_FILE` | 로컬·파일 주입 시 | 기본 `credentials.json`. `GOOGLE_CREDENTIALS_JSON` 이 있으면 파일은 생략 가능 |
| `GOOGLE_REDIRECT_URI` | **프로덕션에서 예** | `https://<실제호스트>/api/oauth/google/callback` (끝 슬래시 없음) |
| `DEPLOY_ENV` | **Render 권장** | `production`이면 Selenium eTL 경로 비활성화·`requirements.txt`만으로 기동. 로컬 브라우저 동기화는 `local`(기본) + [`requirements-dev.txt`](requirements-dev.txt) |
| `APP_NAME` | 아니오 | 기본 `eTL Calendar Sync` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 아니오 | 기본 10080 |
| `OAUTH_STATE_EXPIRE_MINUTES` | 아니오 | 기본 15 |
| `STRIPE_SECRET_KEY` | 아니오 | 결제 연동 시 |
| `STRIPE_WEBHOOK_SECRET` | 아니오 | Stripe 웹훅 검증 시 |
| `ETL_HEADLESS` | 아니오 | Selenium 헤드리스 여부(기본 true) |
| `ETL_HEADED_PAUSE_SEC` | 아니오 | 기본 8.0 |
| `ETL_KEEP_BROWSER_OPEN` | 아니오 | 기본 false |
| `ETL_BROWSER` | 아니오 | `chrome` 등 |
| `ETL_CHROME_DEBUGGER_ADDRESS` | 아니오 | 기본 `127.0.0.1:9222` |

로컬에서만 `http://` OAuth를 쓸 때는 앱이 lifespan에서 `OAUTHLIB_INSECURE_TRANSPORT=1`을 설정합니다. 프로덕션 Render에는 넣지 않습니다.

`credentials.json`은 커밋하지 말고, Render에는 **`GOOGLE_CREDENTIALS_JSON`**(JSON 문자열) 또는 Secret File + `GOOGLE_CLIENT_SECRETS_FILE` 경로로 주입합니다.

### Render Environment 키 체크리스트 (값은 대시보드에서만 설정)

아래 키 이름이 `app/config.py` 필드와 일치하는지 확인하고, Render에 빠짐없이 넣었는지 체크합니다(값은 여기 적지 않음).

| 체크 | 키 이름 |
|:----:|---------|
| ☐ | `APP_SECRET_KEY` |
| ☐ | `CRYPTO_KEY` |
| ☐ | `DATABASE_URL` |
| ☐ | `GOOGLE_CREDENTIALS_JSON` |
| ☐ | `GOOGLE_CLIENT_SECRETS_FILE` |
| ☐ | `GOOGLE_REDIRECT_URI` |
| ☐ | `DEPLOY_ENV` |
| ☐ | `APP_NAME` |
| ☐ | `ACCESS_TOKEN_EXPIRE_MINUTES` |
| ☐ | `OAUTH_STATE_EXPIRE_MINUTES` |
| ☐ | `STRIPE_SECRET_KEY` |
| ☐ | `STRIPE_WEBHOOK_SECRET` |
| ☐ | `ETL_HEADLESS` |
| ☐ | `ETL_HEADED_PAUSE_SEC` |
| ☐ | `ETL_KEEP_BROWSER_OPEN` |
| ☐ | `ETL_BROWSER` |
| ☐ | `ETL_CHROME_DEBUGGER_ADDRESS` |

프로덕션에서 필수에 가까운 것은 `APP_SECRET_KEY`, `CRYPTO_KEY`, **`GOOGLE_CREDENTIALS_JSON` 또는 `GOOGLE_CLIENT_SECRETS_FILE`**, `GOOGLE_REDIRECT_URI`, **`DEPLOY_ENV=production`** 입니다. 나머지는 기본값으로도 기동할 수 있습니다.

**의존성**: Render 빌드는 `pip install -r requirements.txt`만 실행합니다(Selenium 미포함). 로컬에서 「🔐 eTL 로그인」「🔄 전체 동기화」를 쓰려면 `pip install -r requirements-dev.txt`로 Selenium을 추가하세요.

### 프로덕션 SQLite

Render Web Service의 **기본 디스크는 재배포·인스턴스 교체 시 초기화**될 수 있습니다. `sqlite:///./data.db`만 쓰면 사용자·토큰 등이 유실될 수 있으니, **운영**에서는 [Render PostgreSQL](https://render.com/docs/databases) 등 외부 DB와 `DATABASE_URL`을 쓰는 것을 권장합니다. `DATABASE_URL`을 Postgres 등으로 바꿀 때는 해당 드라이버 패키지(예: `psycopg2-binary`)를 `requirements.txt`에 추가하고 URL 스킴을 엔진에 맞게 설정합니다. 스모크·데모만이면 SQLite로도 기동은 가능합니다.

### eTL Selenium·브라우저 스크래핑 (운영 한계)

Render 기본 **Python 네이티브 런타임**에는 Chromium/Chrome이 포함되지 않으며, Selenium용 브라우저 스택을 안정적으로 유지하는 것은 공식 지원 범위를 벗어난 경우가 많습니다. **`DEPLOY_ENV=production`**(권장)이면 API가 브라우저 eTL 경로를 막고 **iCal 간편 동기화·OAuth·정적 UI**만 안내합니다. **eTL 전용 Selenium 스크래핑**은 로컬(`DEPLOY_ENV=local` + `requirements-dev.txt`), Docker 이미지(Chromium + 드라이버 포함), 또는 별도 워커에서 실행하는 것을 권장합니다.

## Google OAuth (경로 고정)

| 단계 | 메서드·경로 |
|------|----------------|
| 인가 | `GET /api/oauth/google/authorize` |
| 콜백 | `GET /api/oauth/google/callback` |

- 구현: `app/routers/google_oauth.py`  
- 설정: `app/config.py`의 `Settings.google_redirect_uri` ← 환경 변수 **`GOOGLE_REDIRECT_URI`**로 덮어씀.  
- `Flow.from_client_secrets_file(..., redirect_uri=settings.google_redirect_uri)`에 동일 값이 쓰입니다.  
- 성공 시 `/?google=connected`로 302, 프론트는 루트 `static/index.html`.  
- 로컬 HTTP OAuth: `app/main.py` lifespan에서 `google_redirect_uri`가 `http://`로 시작할 때만 `OAUTHLIB_INSECURE_TRANSPORT=1` 설정.

### 프로덕션용 `GOOGLE_REDIRECT_URI`

프로덕션에서는 **HTTPS**와 공개 도메인으로 맞춥니다.

```text
https://<실제호스트>/api/oauth/google/callback
```

### 배포·Google OAuth (Render / 프로덕션 호스트)

Build/Start는 위 «**Render Web Service**» 절과 동일합니다. `<실제호스트>`는 대시보드 Public URL에서 **스킴(`https://`)을 뺀 호스트**입니다(예: `my-service.onrender.com`). 아래는 **문자열 완전 일치**·콜백 경로 **끝 슬래시 없음**을 전제로 합니다.

- 루트: `https://<실제호스트>/`
- 프로덕션 `GOOGLE_REDIRECT_URI` (Render env **한 줄**):  
  `https://<실제호스트>/api/oauth/google/callback`

Google Console «승인된 리디렉션 URI»·OAuth JSON `redirect_uris`에도 **위와 동일 한 줄**을 넣습니다.

#### Render에서 할 일 (한 줄 요약)

`GOOGLE_REDIRECT_URI`를 위 형태로 넣고, OAuth JSON·`APP_SECRET_KEY`·`CRYPTO_KEY` 등 필수 변수를 함께 넣은 뒤 **재배포**한다. env만 바꿨으면 서비스 **재시작**이 필요할 수 있다.

#### Google Cloud Console

- «승인된 리디렉션 URI»에 위 **콜백 URI 한 줄**을 **문자 그대로** 등록.
- OAuth 클라이언트 JSON의 `redirect_uris`에도 **동일 문자열** 포함.
- «승인된 JavaScript 원본»은 이 앱이 **서버 OAuth만** 쓰면 보통 필수는 아니다. 문제가 나면 그때 원본 출처를 추가하면 된다.

#### 로컬과 병행 시

Console·JSON `redirect_uris`에는 로컬(`http://127.0.0.1:8000/api/oauth/google/callback` 등)과 프로덕션 URI를 **둘 다** 등록하고, **Render 프로덕션 env**에는 프로덕션 콜백 **한 줄만** 둔다.

#### 스모크

배포 후 브라우저: `https://<실제호스트>/api/oauth/google/authorize` → Google 동의 → `https://<실제호스트>/api/oauth/google/callback` → `https://<실제호스트>/?google=connected` 한 바퀴 확인.

### 검증 체크리스트 (하나라도 틀리면 로그인/콜백 실패)

아래 **세 곳**의 문자열이 **바이트 단위로 동일**해야 합니다(대소문자·`www` 유무·슬래시까지).

1. **Google Cloud Console** → API 및 서비스 → 사용자 인증 정보 → OAuth 2.0 클라이언트 ID → **승인된 리디렉션 URI**
2. 서버에 두는 **OAuth 클라이언트 JSON** (`GOOGLE_CLIENT_SECRETS_FILE`, 기본 `credentials.json`) 내부 **`redirect_uris`** 배열
3. 해당 환경의 **`.env` 또는 호스팅 시크릿**의 **`GOOGLE_REDIRECT_URI`**

### 배포 URL 정합성 (프로덕션)

프로덕션에서 쓰는 `GOOGLE_REDIRECT_URI`는 **사용자가 브라우저 주소창에 보이는 호스트**와 맞아야 합니다.

- **스킴**: 프로덕션은 **`https://`만** (프로덕션에서 `http://` 콜백은 쓰지 않음).
- **호스트**: **`www` 포함 여부**까지 실제 서비스 URL과 동일하게 (`example.com` vs `www.example.com`은 다른 값).
- **경로**: 정확히 **`/api/oauth/google/callback`** (끝에 **트레일링 슬래시 없음**).

### 환경별 분리

- **프로덕션** 서버(또는 프로덕션 `.env`)에는 **프로덕션용 `GOOGLE_REDIRECT_URI` 한 값**만 둡니다.
- **로컬**에는 **로컬용 한 값**만 둡니다.
- 로컬과 프로덕션을 **둘 다** 쓰려면: Google Console과 JSON `redirect_uris`에는 **여러 URI를 등록**하고, **각 서버 환경**에는 그중 **해당 환경에 맞는 URI 하나**만 넣습니다.

### 로컬 + 프로덕션 URI 동시 사용 (예시)

Google Console의 **승인된 리디렉션 URI**에는 여러 개를 등록할 수 있습니다. 예:

- `http://127.0.0.1:8000/api/oauth/google/callback` (로컬)
- `https://<실제호스트>/api/oauth/google/callback` (프로덕션 — 대시보드 URL의 호스트로 치환)

각 환경의 서버에는 해당 환경에 맞는 **`GOOGLE_REDIRECT_URI` 한 값**만 넣고, 쓰는 `credentials.json`의 `redirect_uris`에는 **Console에 등록한 전체 후보**를 맞춰 둡니다.

### 배포 후 OAuth 스모크 테스트

배포 URL을 `https://<실제호스트>`라 할 때, 브라우저에서 순서대로 확인합니다.

1. 주소창에 `https://<실제호스트>/api/oauth/google/authorize` 로 이동한다.
2. Google 동의 화면이 나오고 진행한다.
3. 리디렉션 후 주소가 `https://<실제호스트>/api/oauth/google/callback?...` 형태로 잠시 거친 뒤,
4. 최종적으로 `https://<실제호스트>/?google=connected` 로 돌아오면(302 후) 한 바퀴 성공이다.

실패 시: Console 승인 URI · JSON `redirect_uris` · 배포 `GOOGLE_REDIRECT_URI` · 실제 접속 도메인(`www` 포함 여부)을 다시 대조한다.

## 시크릿·파일 커밋 방지

`.gitignore`에 다음이 포함되어 있습니다: **`.env`**, **`credentials.json`** 등. 저장소에 올리지 마세요.

**배포 시 주입**: 호스팅(Render/Railway/Fly 등)의 **Environment** 또는 **Secret file** 기능에 `APP_SECRET_KEY`, `CRYPTO_KEY`, `GOOGLE_REDIRECT_URI` 등을 넣고, OAuth 클라이언트 JSON은 (1) 시크릿 파일로 마운트하거나 (2) 빌드/시작 스크립트에서 환경변수 내용으로 파일을 생성하는 방식 중 하나를 씁니다. 플랫폼 문서의 «Secret», «Environment variables» 절을 따르면 됩니다.

## 초기 설정

`.env.example`을 복사해 `.env`를 만들고 값을 채웁니다. Google 관련 상세 주석은 `.env.example`에 있습니다. 브라우저 eTL 동기화를 로컬에서 쓸 경우 `pip install -r requirements-dev.txt`를 추가로 실행합니다.

```bash
cp .env.example .env
```
