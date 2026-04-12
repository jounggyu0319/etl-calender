"""
Google Calendar — 사용자별 Credentials(JSON)로 서비스 생성 및 이벤트 추가
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = "primary"
SEOUL = timezone(timedelta(hours=9))


def credentials_from_authorized_user_json(token_json: str) -> Credentials:
    info = json.loads(token_json)
    return Credentials.from_authorized_user_info(info, SCOPES)


def get_calendar_service(token_json: str):
    creds = credentials_from_authorized_user_json(token_json)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def ensure_calendar_service(token_json: str) -> tuple[object, str]:
    """
    만료 시 refresh 후 (service, 최신 credentials JSON) 반환.
    """
    creds = credentials_from_authorized_user_json(token_json)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    updated_json = creds.to_json()
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return service, updated_json


def serialize_credentials(creds: Credentials) -> str:
    return creds.to_json()


def _allday_end_exclusive(start_date: str) -> str:
    """Google Calendar 종일 이벤트: end.date는 배타(다음 날)."""
    d = datetime.strptime(start_date, "%Y-%m-%d").date()
    return (d + timedelta(days=1)).isoformat()


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def parse_deadline(deadline_text: str | None) -> dict | None:
    if not deadline_text:
        return None
    text = deadline_text.strip()

    pattern_ko = r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(오전|오후)\s*(\d{1,2}):(\d{2})"
    match = re.search(pattern_ko, text)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        ampm, hour, minute = match.group(4), int(match.group(5)), int(match.group(6))
        if ampm == "오후" and hour != 12:
            hour += 12
        elif ampm == "오전" and hour == 12:
            hour = 0
        dt = datetime(year, month, day, hour, minute, tzinfo=SEOUL)
        return {"dateTime": dt.isoformat(), "timeZone": "Asia/Seoul"}

    # 한국어 날짜만 (시간 없음) → 종일
    m_ko_day = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", text)
    if m_ko_day:
        tail = text[m_ko_day.end() : m_ko_day.end() + 36]
        # 날짜 직후에 오전/오후가 있으면 시간 있는데 형식이 달라진 경우 → 종일로 오인 방지
        if not re.search(r"(?:오전|오후)", tail):
            y, mo, d = int(m_ko_day.group(1)), int(m_ko_day.group(2)), int(m_ko_day.group(3))
            ds = f"{y:04d}-{mo:02d}-{d:02d}"
            return {"date": ds}

    # English: "May 15, 2026" / "October 1, 2025 2:30 PM" / "15 May 2026"
    m_en = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2}),?\s+(\d{4})\b(?:\s*[,\s]*(\d{1,2}):(\d{2})\s*(AM|PM|am|pm))?",
        text,
        re.I,
    )
    if m_en:
        mon = _MONTHS[m_en.group(1).lower()]
        day, year = int(m_en.group(2)), int(m_en.group(3))
        if m_en.group(4) is None:
            ds = f"{year:04d}-{mon:02d}-{day:02d}"
            return {"date": ds}
        hour, minute = int(m_en.group(4)), int(m_en.group(5))
        ap = (m_en.group(6) or "").lower()
        if ap == "pm" and hour != 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        dt = datetime(year, mon, day, hour, minute, tzinfo=SEOUL)
        return {"dateTime": dt.isoformat(), "timeZone": "Asia/Seoul"}

    m_en2 = re.search(
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
        text,
        re.I,
    )
    if m_en2:
        day = int(m_en2.group(1))
        mon = _MONTHS[m_en2.group(2).lower()]
        year = int(m_en2.group(3))
        ds = f"{year:04d}-{mon:02d}-{day:02d}"
        return {"date": ds}

    # English: "May 15" / "May 15, 2026" (month before day, no comma year optional)
    m_en_short = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2})(?:,?\s*(\d{4}))?\b",
        text,
        re.I,
    )
    if m_en_short:
        mon = _MONTHS[m_en_short.group(1).lower()]
        day = int(m_en_short.group(2))
        year = int(m_en_short.group(3)) if m_en_short.group(3) else datetime.now(SEOUL).year
        ds = f"{year:04d}-{mon:02d}-{day:02d}"
        return {"date": ds}

    # English: "15th of May 2026" / "15th of May"
    m_en_ord = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)\s+of\s+(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"(?:\s*,?\s*(\d{4}))?\b",
        text,
        re.I,
    )
    if m_en_ord:
        day = int(m_en_ord.group(1))
        mon = _MONTHS[m_en_ord.group(2).lower()]
        year = int(m_en_ord.group(3)) if m_en_ord.group(3) else datetime.now(SEOUL).year
        ds = f"{year:04d}-{mon:02d}-{day:02d}"
        return {"date": ds}

    # Numeric: MM/DD/YYYY or M/D/YYYY
    m_slash_full = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if m_slash_full:
        mo, d, y = int(m_slash_full.group(1)), int(m_slash_full.group(2)), int(m_slash_full.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return {"date": f"{y:04d}-{mo:02d}-{d:02d}"}

    # Numeric: MM/DD (연도 없음 → 올해)
    m_slash_short = re.search(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", text)
    if m_slash_short:
        mo, d = int(m_slash_short.group(1)), int(m_slash_short.group(2))
        y = datetime.now(SEOUL).year
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return {"date": f"{y:04d}-{mo:02d}-{d:02d}"}

    # Korean: "5월 15일" (연도 없음 → 올해)
    m_ko_md = re.search(r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일", text)
    if m_ko_md:
        mo, d = int(m_ko_md.group(1)), int(m_ko_md.group(2))
        y = datetime.now(SEOUL).year
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return {"date": f"{y:04d}-{mo:02d}-{d:02d}"}

    # YYYY-MM-DD (시간·T 없음) → 종일
    m_day = re.search(r"(?<![\d-])(\d{4}-\d{2}-\d{2})(?![\dT])", text)
    if m_day:
        return {"date": m_day.group(1)}

    # ISO-8601 (Moodle 영문 로케일 등)
    m_iso = re.search(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2}|Z)?",
        text,
    )
    if m_iso:
        raw = m_iso.group(0)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SEOUL)
        else:
            dt = dt.astimezone(SEOUL)
        return {"dateTime": dt.isoformat(), "timeZone": "Asia/Seoul"}

    return None


def add_assignment_to_calendar(service, assignment: dict) -> bool:
    deadline = parse_deadline(assignment.get("deadline"))
    kind = assignment.get("activity_type") or "assign"
    if kind == "quiz":
        label = "퀴즈·시험"
        desc_kind = "eTL 퀴즈/시험(Moodle). 중간·기말 여부는 제목·강의 공지를 확인하세요."
    elif kind == "announcement_midterm":
        label = "공지·중간"
        desc_kind = "eTL 강의 공지/포럼 등에서 자동 감지한 중간고사 관련 문구입니다. 일정은 반드시 공지 원문을 확인하세요."
    elif kind == "announcement_final":
        label = "공지·기말"
        desc_kind = "eTL 강의 공지/포럼 등에서 자동 감지한 기말고사 관련 문구입니다. 일정은 반드시 공지 원문을 확인하세요."
    elif kind == "ical_feed":
        label = "iCal"
        desc_kind = "eTL Moodle «캘린더보내기» 구독 URL에서 가져온 일정입니다. 세부는 myetl 캘린더 원본을 확인하세요."
    elif kind == "forum_notice":
        label = "공지·포럼"
        desc_kind = "eTL 강의 공지(포럼)에서 키워드로 찾은 글입니다. 일정·마감은 원문을 반드시 확인하세요."
    else:
        label = "과제"
        desc_kind = "eTL 과제"

    if not deadline:
        today = datetime.now(SEOUL).strftime("%Y-%m-%d")
        start = {"date": today}
        end = {"date": _allday_end_exclusive(today)}
    elif "date" in deadline:
        d0 = deadline["date"]
        start = {"date": d0}
        end = {"date": _allday_end_exclusive(d0)}
    else:
        start_dt = datetime.fromisoformat(deadline["dateTime"])
        end_dt = start_dt + timedelta(hours=1)
        start = deadline
        end = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"}

    extra_desc = (assignment.get("description_extra") or "").strip()
    desc_body = f"{desc_kind}\n{extra_desc}\n링크: {assignment.get('url', '')}" if extra_desc else f"{desc_kind}\n링크: {assignment.get('url', '')}"

    event = {
        "summary": f"[{assignment['subject']}] [{label}] {assignment['title']}",
        "description": desc_body[:8000],
        "start": start,
        "end": end,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60 * 24},
                {"method": "popup", "minutes": 60},
            ],
        },
        "colorId": "11",
    }
    try:
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return True
    except Exception:
        return False


def sync_assignments_to_calendar(service, assignments: list[dict]) -> int:
    ok = 0
    for a in assignments:
        if add_assignment_to_calendar(service, a):
            ok += 1
    return ok
