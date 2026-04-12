import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import Settings

# passlib 1.7.4 + bcrypt 4.x 버전 감지 경고 억제 (동작에는 영향 없음)
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, settings: Settings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "typ": "access"}
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> str | None:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
        if payload.get("typ") != "access":
            return None
        sub = payload.get("sub")
        return str(sub) if sub is not None else None
    except JWTError:
        return None


def create_google_oauth_state(user_id: int, settings: Settings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.oauth_state_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire, "typ": "google_oauth"}
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def decode_google_oauth_state(token: str, settings: Settings) -> int | None:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
        if payload.get("typ") != "google_oauth":
            return None
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None


def get_fernet(settings: Settings) -> Fernet:
    return Fernet(settings.crypto_key.encode())


def encrypt_text(plain: str | None, settings: Settings) -> str | None:
    if plain is None:
        return None
    f = get_fernet(settings)
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_text(token: str | None, settings: Settings) -> str | None:
    if not token:
        return None
    f = get_fernet(settings)
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None
