"""동기화 로그 — Google Calendar에 추가된 항목을 sync_logs 테이블에 기록."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import SyncLog
from app.services.calendar_service import format_calendar_event_summary

logger = logging.getLogger(__name__)

_MAX_LOGS_PER_USER = 200


def prune_sync_logs(db: Session, user_id: int) -> None:
    """사용자 sync_logs를 최근 _MAX_LOGS_PER_USER건으로 유지."""
    try:
        from sqlalchemy import delete, func, select

        count = db.scalar(
            select(func.count()).select_from(SyncLog).where(SyncLog.user_id == user_id)
        ) or 0
        if count <= _MAX_LOGS_PER_USER:
            return
        oldest_ids = db.scalars(
            select(SyncLog.id)
            .where(SyncLog.user_id == user_id)
            .order_by(SyncLog.synced_at.asc())
            .limit(count - _MAX_LOGS_PER_USER)
        ).all()
        if oldest_ids:
            db.execute(delete(SyncLog).where(SyncLog.id.in_(oldest_ids)))
    except Exception as exc:
        logger.warning("[sync_log] 로그 정리 실패 (user_id=%d): %s", user_id, exc)


def log_sync_item(db: Session, user_id: int, assignment: dict) -> None:
    """캘린더에 성공적으로 추가된 항목을 sync_logs에 기록.

    오래된 항목은 _MAX_LOGS_PER_USER 초과 시 자동 정리.
    """
    try:
        event_title = format_calendar_event_summary(assignment)[:1020]
        subject = str(assignment.get("subject") or "").strip()[:256]
        activity_type = str(assignment.get("activity_type") or "assign").strip()[:64]

        # deadline_date: ISO 날짜 문자열만 저장 (시간 제외)
        raw_deadline = str(assignment.get("deadline") or "").strip()
        deadline_date: str | None = None
        if raw_deadline:
            # ISO 형식이면 날짜 부분만 추출
            if "T" in raw_deadline:
                deadline_date = raw_deadline[:10]
            elif len(raw_deadline) >= 10 and raw_deadline[4] == "-":
                deadline_date = raw_deadline[:10]

        entry = SyncLog(
            user_id=user_id,
            synced_at=datetime.now(timezone.utc),
            event_title=event_title,
            subject=subject,
            activity_type=activity_type,
            deadline_date=deadline_date,
        )
        db.add(entry)
        db.flush()  # ID 확보 (commit은 호출부에서)
    except Exception as exc:
        logger.warning("[sync_log] 로그 저장 실패 (user_id=%d): %s", user_id, exc)
