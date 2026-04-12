from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import EtlCredentialsUpdate, MoodleCalendarFeedUpdate, UserOut
from app.security import encrypt_text
from app.serializers import user_to_out
from app.services.moodle_ics import validate_moodle_calendar_feed_url

router = APIRouter()


@router.get("/", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)) -> UserOut:
    return user_to_out(user)


@router.patch("/etl", response_model=UserOut)
def update_etl_credentials(
    body: EtlCredentialsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    user.etl_username_enc = encrypt_text(body.etl_username.strip(), settings)
    user.etl_password_enc = encrypt_text(body.etl_password.strip("\r\n"), settings)
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
