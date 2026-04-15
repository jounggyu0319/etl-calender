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
    # SQLite: 기존 DB에 새 컬럼 추가 (create_all만으로는 ALTER 안 됨)
    if _is_sqlite:
        insp = inspect(engine)
        if insp.has_table("users"):
            cols = {c["name"] for c in insp.get_columns("users")}
            if "moodle_calendar_feed_enc" not in cols:
                with engine.begin() as conn:
                    conn.execute(
                        text("ALTER TABLE users ADD COLUMN moodle_calendar_feed_enc TEXT")
                    )
