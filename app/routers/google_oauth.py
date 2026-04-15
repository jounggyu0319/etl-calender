import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.google_oauth_client import load_google_oauth_client_dict
from app.models import User
from app.security import create_google_oauth_state, decode_google_oauth_state, encrypt_text
from calendar_service import SCOPES

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/authorize")
def google_authorize(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    try:
        client_cfg = load_google_oauth_client_dict(settings)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"GOOGLE_CREDENTIALS_JSON 이 올바른 JSON이 아닙니다: {e}",
        ) from e
    if client_cfg is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth 클라이언트 설정이 없습니다. "
                "Render에는 환경 변수 GOOGLE_CREDENTIALS_JSON(전체 JSON), "
                "로컬에는 credentials.json 또는 동일 변수를 설정하세요."
            ),
        )
    state = create_google_oauth_state(user.id, settings)
    flow = Flow.from_client_config(
        client_cfg,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return {"authorization_url": authorization_url}


@router.get("/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    state = request.query_params.get("state")
    user_id = decode_google_oauth_state(state or "", settings)
    if user_id is None:
        return RedirectResponse(url="/?google=bad_state", status_code=302)

    if request.query_params.get("error"):
        return RedirectResponse(url="/?google=denied", status_code=302)

    try:
        client_cfg = load_google_oauth_client_dict(settings)
    except json.JSONDecodeError:
        return RedirectResponse(url="/?google=bad_client_json", status_code=302)
    if client_cfg is None:
        return RedirectResponse(url="/?google=no_client", status_code=302)

    flow = Flow.from_client_config(
        client_cfg,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )
    # Render 등 HTTPS 프록시 환경: uvicorn이 내부적으로 http://로 보더라도
    # Google이 돌려보낸 URL은 https://여야 하므로 스킴을 보정한다.
    callback_url = str(request.url)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto == "https" and callback_url.startswith("http://"):
        callback_url = "https://" + callback_url[7:]
    elif settings.google_redirect_uri.startswith("https://") and callback_url.startswith("http://"):
        callback_url = "https://" + callback_url[7:]
    try:
        flow.fetch_token(authorization_response=callback_url)
    except Exception:
        logger.exception("Google OAuth token fetch 실패 | url=%s", callback_url)
        return RedirectResponse(url="/?google=token_error", status_code=302)

    user = db.get(User, user_id)
    if user is None:
        return RedirectResponse(url="/?google=no_user", status_code=302)

    creds = flow.credentials
    raw = creds.to_json()

    try:
        user.google_creds_enc = encrypt_text(raw, settings)
    except Exception:
        logger.exception(
            "Google OAuth 저장 실패(암호화) | user_id=%s | crypto_key_len=%s",
            user_id,
            len(settings.crypto_key or ""),
        )
        return RedirectResponse(url="/?google=save_encrypt_error", status_code=302)

    try:
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Google OAuth 저장 실패(DB commit) | user_id=%s", user_id)
        return RedirectResponse(url="/?google=save_db_error", status_code=302)

    return RedirectResponse(url="/?google=connected", status_code=302)
