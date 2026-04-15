from app.models import User
from app.schemas import UserOut


def user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        plan=user.plan,
        has_etl_credentials=bool(user.etl_username_enc and user.etl_password_enc),
        has_moodle_calendar_feed=bool(user.moodle_calendar_feed_enc),
        has_google=bool(user.google_creds_enc),
        auto_sync_enabled=bool(user.auto_sync_enabled),
        last_auto_sync_at=user.last_auto_sync_at,
    )
