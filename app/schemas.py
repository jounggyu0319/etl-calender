from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    plan: str
    has_moodle_calendar_feed: bool
    has_google: bool
    has_canvas_token: bool = False
    assign_color_id: str = "9"
    exam_color_id: str = "11"
    auto_sync_enabled: bool = False
    last_auto_sync_at: datetime | None = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CanvasTokenUpdate(BaseModel):
    """myetl Canvas REST API 액세스 토큰. 빈 문자열이면 저장 제거."""

    token: str = Field(default="", max_length=4096)


class AutoSyncUpdate(BaseModel):
    """자동 동기화 on/off."""

    enabled: bool


class ColorSettingsUpdate(BaseModel):
    assign_color_id: str = Field(default="9", pattern="^([1-9]|1[01])$")
    exam_color_id: str = Field(default="11", pattern="^([1-9]|1[01])$")


class MoodleCalendarFeedUpdate(BaseModel):
    """Moodle 캘린더 «URL 주소 가져오기»로 받은 구독 링크. 빈 문자열이면 저장 제거."""

    feed_url: str = Field(default="", max_length=4096)


class ClientSyncItem(BaseModel):
    """확장·데스크톱 WebView가 myetl에서 수집한 단일 활동(과제/퀴즈/시험 공지 등)."""

    id: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=2048)
    activity_type: str = Field(default="assign", max_length=64)
    deadline: str = Field(default="", max_length=2048)
    posted_at: str = Field(default="", max_length=2048)
    description_extra: str = Field(default="", max_length=8000)


class ClientSyncImport(BaseModel):
    items: list[ClientSyncItem] = Field(default_factory=list, max_length=500)


class SyncProgressOut(BaseModel):
    """전체 동기화(continue) 진행 — 폴링용."""

    running: bool = False
    phase: str = ""
    course_index: int = 0
    course_total: int = 0
    course_name: str = ""


class SyncResult(BaseModel):
    """동기화 결과 + eTL 쪽 스캔 검증 요약."""

    new_assignments: int
    calendar_events_created: int
    ics_events_created: int = 0
    message: str | None = None
    login_ok: bool = True
    courses_found: int = 0
    assign_links_found: int = 0
    quiz_links_found: int = 0
    announcement_keyword_hits: int = 0
    login_note: str | None = None
    # True: eTL용 브라우저가 열렸고, myetl 로그인·MFA 후「동기화 실행」을 눌러야 함
    etl_awaiting_user: bool = False
    # True: Selenium으로 강의 목록(get_courses)까지 실제 스캔함. False면 iCal-only 등으로 courses_found 미검사
    course_list_scanned: bool = False
    # 간편 동기화(iCal) 전용 — Selenium과 섞지 않음
    ical_feed_configured: bool = False
    ical_sync_attempted: bool = False
    ical_sync_ok: bool | None = None
    # True: POST /api/sync/ (간편 동기화) 응답 — 강의 스캔 문구와 섞지 않음
    ical_ui_context: bool = False
    # True: POST /api/sync/canvas — 서버 Canvas API 동기화
    canvas_server_context: bool = False


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    synced_at: datetime
    event_title: str
    subject: str
    activity_type: str
    deadline_date: str | None = None


class BillingStatus(BaseModel):
    plan: str
    stripe_customer_id: str | None
    billing_portal_available: bool
