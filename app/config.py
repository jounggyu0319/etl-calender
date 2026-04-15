from cryptography.fernet import Fernet
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # `production`: Selenium eTL 경로 비활성화(Render 등). `local`: 브라우저 동기화 가능( selenium 은 dev 요구 ).
    deploy_env: str = "local"

    app_name: str = "eTL Calendar Sync"
    app_secret_key: str
    crypto_key: str
    database_url: str = "sqlite:///./data.db"

    google_client_secrets_file: str = "credentials.json"
    # Render 등: Google Cloud에서 받은 OAuth 클라이언트 JSON 전체를 문자열로 (파일 없이)
    google_credentials_json: str | None = None
    google_redirect_uri: str = "http://127.0.0.1:8000/api/oauth/google/callback"

    access_token_expire_minutes: int = 60 * 24 * 7
    oauth_state_expire_minutes: int = 15

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None

    # eTL 동기화 시 Selenium 헤드리스. false면 브라우저 창이 보여 로그인 차단 여부 확인에 유리.
    etl_headless: bool = True
    # ETL_HEADLESS=false 일 때, 끝나고 driver.quit() 하기 전 대기(초). 창이 바로 사라지는 것을 완화.
    etl_headed_pause_sec: float = 8.0
    # 창이 보이면(헤드리스 아님) 동기화 후에도 브라우저를 기본으로 닫지 않음. headless에서만 창을 남기려면 true(디버깅).
    etl_keep_browser_open: bool = False
    # Selenium 브라우저: chrome | edge | firefox | safari | system (mac→safari, win→edge, 그 외 chrome)
    etl_browser: str = "chrome"
    # headed Chrome 원격 디버깅 주소 (`.env`의 ETL_CHROME_DEBUGGER_ADDRESS)
    etl_chrome_debugger_address: str = "127.0.0.1:9222"

    @field_validator("etl_headless", mode="before")
    @classmethod
    def _parse_etl_headless(cls, v):
        if isinstance(v, bool):
            return v
        if v is None or v == "":
            return True
        s = str(v).strip().lower()
        return s not in ("0", "false", "no", "off")

    @field_validator("etl_headed_pause_sec", mode="before")
    @classmethod
    def _parse_etl_headed_pause_sec(cls, v):
        if v is None or v == "":
            return 8.0
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return 8.0

    @field_validator("etl_keep_browser_open", mode="before")
    @classmethod
    def _parse_etl_keep_browser_open(cls, v):
        if isinstance(v, bool):
            return v
        if v is None or v == "":
            return False
        s = str(v).strip().lower()
        return s in ("1", "true", "yes", "on")

    @field_validator("etl_chrome_debugger_address", mode="before")
    @classmethod
    def _parse_etl_chrome_debugger_address(cls, v):
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "127.0.0.1:9222"
        return str(v).strip()

    @field_validator("google_credentials_json", mode="before")
    @classmethod
    def _empty_google_credentials_json(cls, v):
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        return str(v).strip()

    @field_validator("crypto_key", mode="before")
    @classmethod
    def _validate_crypto_key(cls, v):
        s = str(v or "").strip()
        # Fail-fast: 잘못된 Fernet 키는 OAuth 토큰 저장(encrypt) 시점에 save_error를 유발한다.
        Fernet(s.encode())
        return s

    @field_validator("deploy_env", mode="before")
    @classmethod
    def _parse_deploy_env(cls, v):
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return "local"
        s = str(v).strip().lower()
        if s in ("production", "prod", "render", "cloud"):
            return "production"
        return "local"

    @field_validator("etl_browser", mode="before")
    @classmethod
    def _parse_etl_browser(cls, v):
        if v is None or v == "":
            return "chrome"
        s = str(v).strip().lower()
        allowed = frozenset({"chrome", "edge", "firefox", "safari", "system"})
        if s not in allowed:
            return "chrome"
        return s


def get_settings() -> Settings:
    return Settings()
