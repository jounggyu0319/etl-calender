from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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

    seen_assignment_ids: Mapped[str] = mapped_column(Text, default="[]")

    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    last_auto_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
