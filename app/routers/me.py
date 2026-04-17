import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import (
    AutoSyncUpdate,
    CanvasTokenUpdate,
    ColorSettingsUpdate,
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


@router.patch("/color-settings", response_model=UserOut)
def update_color_settings(
    body: ColorSettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    user.assign_color_id = body.assign_color_id
    user.exam_color_id = body.exam_color_id
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_out(user)


@router.get("/check-connections")
def check_connections(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Google / Canvas / 구독 URL 연결 상태를 실제 API 호출로 확인."""
    result: dict[str, dict] = {
        "google": {"ok": False, "error": None},
        "canvas": {"ok": False, "error": None},
        "feed": {"ok": False, "error": None},
    }

    # ── Google Calendar ──
    google_json = decrypt_text(user.google_creds_enc, settings)
    if not google_json:
        result["google"]["error"] = "연결되지 않았어요."
    else:
        try:
            from calendar_service import ensure_calendar_service, probe_calendar_access  # noqa: PLC0415
            service, _ = ensure_calendar_service(google_json)
            err = probe_calendar_access(service)
            if err:
                result["google"]["error"] = "토큰이 만료됐거나 권한이 없어요."
            else:
                result["google"]["ok"] = True
        except Exception as exc:
            result["google"]["error"] = str(exc)[:120]

    # ── Canvas API 토큰 ──
    canvas_token = decrypt_text(user.canvas_token_enc, settings)
    if not canvas_token:
        result["canvas"]["error"] = "토큰이 저장되지 않았어요."
    else:
        try:
            req = urllib.request.Request(
                "https://myetl.snu.ac.kr/api/v1/courses?per_page=1&enrollment_state=active",
                headers={"Authorization": f"Bearer {canvas_token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read()
            result["canvas"]["ok"] = True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                result["canvas"]["error"] = "토큰이 유효하지 않아요. myetl에서 재발급 후 저장해 주세요."
            else:
                result["canvas"]["error"] = f"Canvas API 오류 (HTTP {exc.code})"
        except Exception as exc:
            result["canvas"]["error"] = str(exc)[:120]

    # ── 구독 URL ──
    feed_url = decrypt_text(user.moodle_calendar_feed_enc, settings)
    if not feed_url:
        result["feed"]["error"] = "저장된 구독 URL이 없어요."
    else:
        try:
            req = urllib.request.Request(feed_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as resp:
                status = resp.status
            if status < 400:
                result["feed"]["ok"] = True
            else:
                result["feed"]["error"] = f"URL 응답 오류 (HTTP {status})"
        except urllib.error.HTTPError as exc:
            result["feed"]["error"] = f"URL 응답 오류 (HTTP {exc.code})"
        except Exception as exc:
            result["feed"]["error"] = str(exc)[:120]

    return result


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
