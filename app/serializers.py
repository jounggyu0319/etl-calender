from app.models import User
from app.schemas import UserOut


def user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        plan=user.plan,
        has_moodle_calendar_feed=bool(user.moodle_calendar_feed_enc),
        has_google=bool(user.google_creds_enc),
        has_canvas_token=bool(user.canvas_token_enc),
        assign_color_id=user.assign_color_id or "9",
        exam_color_id=user.exam_color_id or "11",
        auto_sync_enabled=bool(user.auto_sync_enabled),
        last_auto_sync_at=user.last_auto_sync_at,
    )
