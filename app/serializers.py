from __future__ import annotations

from app.models import User
from app.schemas import UserOut


def user_to_out(user: User, moodle_feed_url: str | None = None) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        plan=user.plan,
        has_moodle_calendar_feed=bool(user.moodle_calendar_feed_enc),
        moodle_calendar_feed_url=moodle_feed_url,
        has_google=bool(user.google_creds_enc),
        has_canvas_token=bool(user.canvas_token_enc),
        auto_sync_enabled=bool(user.auto_sync_enabled),
        last_auto_sync_at=user.last_auto_sync_at,
        last_sync_ok=user.last_sync_ok,
        conn_checked_at=user.conn_checked_at,
        google_conn_ok=user.google_conn_ok,
        ical_conn_ok=user.ical_conn_ok,
        canvas_conn_ok=user.canvas_conn_ok,
    )
