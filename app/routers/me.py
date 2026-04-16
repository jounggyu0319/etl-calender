from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import (
    AutoSyncUpdate,
    CanvasTokenUpdate,
    MoodleCalendarFeedUpdate,
    UserOut,
)
from app.security import decrypt_text, encrypt_text
from app.serializers import user_to_out
from app.services.moodle_ics import validate_moodle_calendar_feed_url

router = APIRouter()


@router.get("/", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)) -> UserOut:
    return user_to_out(user)


@router.patch("/canvas-token", response_model=UserOut)
def update_canvas_token(
    body: CanvasTokenUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    raw = (body.token or "").strip()
    if not raw:
        user.canvas_token_enc = None
    else:
        user.canvas_token_enc = encrypt_text(raw, settings)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_out(user)


@router.patch("/auto-sync", response_model=UserOut)
def update_auto_sync(
    body: AutoSyncUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    user.auto_sync_enabled = bool(body.enabled)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_out(user)


@router.post("/check-connections", response_model=UserOut)
def check_connections(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    """Google·구독 URL·Canvas 토큰 연결 상태를 실제 API 호출로 확인하고 DB에 저장."""
    google_ok: bool | None = None
    ical_ok: bool | None = None
    canvas_ok: bool | None = None

    # Google
    google_json = decrypt_text(user.google_creds_enc, settings)
    if google_json:
        try:
            from calendar_service import ensure_calendar_service, probe_calendar_access
            service, fresh_json = ensure_calendar_service(google_json)
            err = probe_calendar_access(service)
            google_ok = err is None
            if fresh_json != google_json:
                user.google_creds_enc = encrypt_text(fresh_json, settings)
        except Exception:
            google_ok = False

    # 구독 URL (iCal fetch)
    feed_plain = decrypt_text(user.moodle_calendar_feed_enc, settings) if user.moodle_calendar_feed_enc else ""
    if feed_plain:
        try:
            r = requests.get(feed_plain.strip(), timeout=8)
            ical_ok = r.status_code == 200 and "BEGIN:VCALENDAR" in r.text[:500]
        except Exception:
            ical_ok = False

    # Canvas 토큰
    canvas_token = decrypt_text(user.canvas_token_enc, settings) if user.canvas_token_enc else ""
    if canvas_token:
        try:
            r = requests.get(
                "https://myetl.snu.ac.kr/api/v1/courses?per_page=1&enrollment_state=active",
                headers={"Authorization": f"Bearer {canvas_token}"},
                timeout=8,
            )
            canvas_ok = r.status_code == 200
        except Exception:
            canvas_ok = False

    user.conn_checked_at = datetime.now(timezone.utc)
    user.google_conn_ok = google_ok
    user.ical_conn_ok = ical_ok
    user.canvas_conn_ok = canvas_ok
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_out(user)


@router.patch("/moodle-calendar-feed", response_model=UserOut)
def update_moodle_calendar_feed(
    body: MoodleCalendarFeedUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    raw = (body.feed_url or "").strip()
    if not raw:
        user.moodle_calendar_feed_enc = None
    else:
        try:
            safe = validate_moodle_calendar_feed_url(raw)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        user.moodle_calendar_feed_enc = encrypt_text(safe, settings)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_out(user)
