"""주기적 자동 동기화: Moodle iCal 구독 URL → Google Calendar (서버 스케줄러)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import User
from app.security import decrypt_text
from app.services.sync_runner import (
    _commit_seen_and_google_with_settings,
    _ical_merge_only,
    _seen_set,
)

logger = logging.getLogger(__name__)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _should_run_now(user: User, now: datetime) -> bool:
    hours = max(1, int(user.auto_sync_interval_hours or 24))
    last = _utc(user.last_auto_sync_at)
    if last is None:
        return True
    return now - last >= timedelta(hours=hours)


def run_auto_sync_for_user(db: Session, user: User, settings: Settings) -> None:
    """단일 유저 iCal → Google. Google·구독 URL 없으면 조용히 return."""
    google_json = decrypt_text(user.google_creds_enc, settings)
    if not google_json:
        logger.debug("auto_sync skip user_id=%s: no Google credentials", user.id)
        return
    feed_plain = ""
    if user.moodle_calendar_feed_enc:
        feed_plain = (decrypt_text(user.moodle_calendar_feed_enc, settings) or "").strip()
    if not feed_plain:
        logger.debug("auto_sync skip user_id=%s: no Moodle iCal URL", user.id)
        return

    merged_seen = _seen_set(user)
    (
        merged_seen,
        google_json,
        google_changed,
        _ics_created,
        ics_err,
        _cfg,
        _ok,
    ) = _ical_merge_only(user, settings, merged_seen, google_json)

    user.last_auto_sync_at = datetime.now(timezone.utc)
    _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)
    if ics_err:
        logger.warning("auto_sync user_id=%s iCal note: %s", user.id, ics_err)


def run_auto_sync_all() -> None:
    """자동 동기화 대상 유저 순회. 개별 실패는 로그만 남기고 다음 유저 진행."""
    settings = get_settings()
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        ids = db.scalars(select(User.id).where(User.auto_sync_enabled.is_(True))).all()
        for uid in ids:
            try:
                u = db.get(User, uid)
                if u is None or not u.auto_sync_enabled:
                    continue
                if not _should_run_now(u, now):
                    continue
                run_auto_sync_for_user(db, u, settings)
            except Exception:
                logger.exception("auto_sync failed user_id=%s", uid)
                db.rollback()
    finally:
        db.close()
