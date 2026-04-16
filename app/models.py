from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    # free | pro | trial — 결제 연동 전까지 문자열로 관리
    plan: Mapped[str] = mapped_column(String(32), default="free")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Moodle «캘린더보내기» 구독 URL(export.php?…&authtoken=…). https·학교 호스트만 허용.
    moodle_calendar_feed_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_creds_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # myetl Canvas «새 액세스 토큰» (서버에서 REST API 호출용, 암호화 저장)
    canvas_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    last_auto_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_sync_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # 연결 상태 수동 확인 결과
    conn_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_conn_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ical_conn_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    canvas_conn_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class SyncLog(Base):
    """Google Calendar에 실제 추가된 항목 로그 (사용자별, 최근 200건 유지)."""

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    event_title: Mapped[str] = mapped_column(String(1024))
    subject: Mapped[str] = mapped_column(String(256), default="")
    activity_type: Mapped[str] = mapped_column(String(64), default="assign")
    deadline_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
