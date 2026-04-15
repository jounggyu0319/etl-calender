from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
_db_url = (settings.database_url or "").strip()
_is_sqlite = _db_url.lower().startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,  # Neon 등 idle 후 끊긴 연결 재검증
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # 기존 DB에 새 컬럼 추가 (create_all만으로는 ALTER 안 됨)
    insp = inspect(engine)
    if not insp.has_table("users"):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    stmts: list[str] = []
    if "moodle_calendar_feed_enc" not in cols:
        stmts.append("ALTER TABLE users ADD COLUMN moodle_calendar_feed_enc TEXT")
    if "auto_sync_enabled" not in cols:
        stmts.append(
            "ALTER TABLE users ADD COLUMN auto_sync_enabled INTEGER DEFAULT 0 NOT NULL"
            if _is_sqlite
            else "ALTER TABLE users ADD COLUMN auto_sync_enabled BOOLEAN DEFAULT FALSE NOT NULL"
        )
    if "auto_sync_interval_hours" not in cols:
        stmts.append(
            "ALTER TABLE users ADD COLUMN auto_sync_interval_hours INTEGER DEFAULT 24 NOT NULL"
        )
    if "last_auto_sync_at" not in cols:
        stmts.append(
            "ALTER TABLE users ADD COLUMN last_auto_sync_at DATETIME"
            if _is_sqlite
            else "ALTER TABLE users ADD COLUMN last_auto_sync_at TIMESTAMP WITH TIME ZONE"
        )
    if "canvas_token_enc" not in cols:
        stmts.append("ALTER TABLE users ADD COLUMN canvas_token_enc TEXT")
    for sql in stmts:
        with engine.begin() as conn:
            conn.execute(text(sql))
