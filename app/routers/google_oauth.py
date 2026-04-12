import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.security import create_google_oauth_state, decode_google_oauth_state, encrypt_text
from calendar_service import SCOPES

router = APIRouter()


@router.get("/authorize")
def google_authorize(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    if not os.path.isfile(settings.google_client_secrets_file):
        raise HTTPException(
            status_code=503,
            detail="서버에 Google OAuth 클라이언트 파일(credentials.json)이 없습니다.",
        )
    state = create_google_oauth_state(user.id, settings)
    flow = Flow.from_client_secrets_file(
        settings.google_client_secrets_file,
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

    if not os.path.isfile(settings.google_client_secrets_file):
        return RedirectResponse(url="/?google=no_client", status_code=302)

    flow = Flow.from_client_secrets_file(
        settings.google_client_secrets_file,
        scopes=SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )
    try:
        flow.fetch_token(authorization_response=str(request.url))
    except Exception:
        return RedirectResponse(url="/?google=token_error", status_code=302)

    user = db.get(User, user_id)
    if user is None:
        return RedirectResponse(url="/?google=no_user", status_code=302)

    creds = flow.credentials
    raw = creds.to_json()
    # 이미 저장된 토큰이 있으면 병합할 필요 없음 — 전체 교체
    user.google_creds_enc = encrypt_text(raw, settings)
    db.add(user)
    db.commit()

    return RedirectResponse(url="/?google=connected", status_code=302)
