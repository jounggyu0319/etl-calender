"""
Google Calendar — 사용자별 Credentials(JSON)로 서비스 생성 및 이벤트 추가
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.services.calendar_service import (
    format_calendar_event_description,
    format_calendar_event_summary,
    normalize_course_display_name,
)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = "primary"
SEOUL = timezone(timedelta(hours=9))
_CAL_LOG = logging.getLogger(__name__)
PRIVATE_ETL_KEY = "etl_id"


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


def _etl_id_for_calendar(assignment: dict) -> str:
    e = str(assignment.get("id") or "").strip()
    if e:
        return e
    u = str(assignment.get("url") or "")
    t = str(assignment.get("title") or "")
    h = hashlib.sha256(f"{u}|{t}".encode("utf-8")).hexdigest()[:40]
    return f"etl:noid:{h}"


def _resolve_google_event_start_end(assignment: dict) -> tuple[dict, dict]:
    """due_at → parse_deadline; 없으면 posted_at(종일); 둘 다 없으면 오늘(종일). 시각 이벤트는 1시간."""
    raw_deadline = assignment.get("deadline")
    d = None
    if raw_deadline is not None and str(raw_deadline).strip():
        d = parse_deadline(str(raw_deadline).strip())
    if not d:
        posted = assignment.get("posted_at")
        if posted is not None and str(posted).strip():
            raw = str(posted).strip().replace("Z", "+00:00")
            try:
                pdt = datetime.fromisoformat(raw)
                if pdt.tzinfo is None:
                    pdt = pdt.replace(tzinfo=timezone.utc)
                pdt = pdt.astimezone(SEOUL)
                ds = pdt.strftime("%Y-%m-%d")
                d = {"date": ds}
            except ValueError:
                d = None
    if not d:
        today = datetime.now(SEOUL).strftime("%Y-%m-%d")
        d = {"date": today}
    if "date" in d:
        d0 = d["date"]
        start = {"date": d0}
        end = {"date": _allday_end_exclusive(d0)}
        return start, end
    start_dt = datetime.fromisoformat(d["dateTime"])
    end_dt = start_dt + timedelta(hours=1)
    start = d
    end = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"}
    return start, end


def calendar_event_exists_with_etl_id(service, etl_id: str) -> bool:
    """primary 캘린더에 extendedProperties.private.etl_id 가 일치하는 일정이 있으면 True."""
    if not etl_id:
        return False
    try:
        resp = (
            service.events()
            .list(
                calendarId=CALENDAR_ID,
                privateExtendedProperty=f"{PRIVATE_ETL_KEY}={etl_id}",
                maxResults=5,
                singleEvents=True,
            )
            .execute()
        )
        return bool(resp.get("items"))
    except Exception as exc:
        _CAL_LOG.warning("Calendar events.list(etl_id) 실패: %s", exc)
        return False


def probe_calendar_access(service) -> str | None:
    """Google Calendar API 접근 가능 여부 확인. 오류 시 오류 메시지 반환, 정상이면 None."""
    try:
        service.events().list(calendarId=CALENDAR_ID, maxResults=1).execute()
        return None
    except Exception as exc:
        msg = str(exc)
        print(f"[ETL] Google Calendar 접근 오류: {msg}", flush=True)
        _CAL_LOG.error("Google Calendar 접근 오류: %s", msg)
        return msg


def _insert_assignment_calendar_event(service, assignment: dict, etl_id: str) -> bool:
    start, end = _resolve_google_event_start_end(assignment)

    summary = format_calendar_event_summary(assignment)[:1020]
    desc_body = format_calendar_event_description(assignment)

    event = {
        "summary": summary,
        "description": desc_body,
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
        "extendedProperties": {"private": {PRIVATE_ETL_KEY: etl_id[:1024]}},
    }
    try:
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return True
    except Exception as exc:
        msg = str(exc)
        print(f"[ETL] Calendar event insert 실패 (etl_id={etl_id}): {msg}", flush=True)
        _CAL_LOG.exception("Calendar event insert 실패 (etl_id=%s): %s", etl_id, exc)
        return False


def add_assignment_to_calendar(service, assignment: dict) -> bool:
    etl_id = _etl_id_for_calendar(assignment)
    if calendar_event_exists_with_etl_id(service, etl_id):
        return True
    return _insert_assignment_calendar_event(service, assignment, etl_id)


def insert_assignment_calendar_if_absent(service, assignment: dict) -> bool:
    """동일 etl_id 일정이 Google에 없을 때만 insert. 새로 만든 경우에만 True."""
    etl_id = _etl_id_for_calendar(assignment)
    exists = calendar_event_exists_with_etl_id(service, etl_id)
    print(f"[ETL] check etl_id={etl_id[:20]}... exists={exists}", flush=True)
    if exists:
        return False
    result = _insert_assignment_calendar_event(service, assignment, etl_id)
    print(f"[ETL] insert result={result} etl_id={etl_id[:20]}...", flush=True)
    return result


def insert_assignment_calendar_if_absent_v2(
    service, assignment: dict
) -> tuple[bool, bool, str | None]:
    """동일 etl_id 일정이 Google에 없을 때만 insert.
    반환: (inserted, skipped_existing, error_msg)
    """
    etl_id = _etl_id_for_calendar(assignment)
    exists = calendar_event_exists_with_etl_id(service, etl_id)
    print(f"[ETL] check etl_id={etl_id[:20]}... exists={exists}", flush=True)
    if exists:
        return False, True, None

    start, end = _resolve_google_event_start_end(assignment)
    summary = format_calendar_event_summary(assignment)[:1020]
    desc_body = format_calendar_event_description(assignment)
    event = {
        "summary": summary,
        "description": desc_body,
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
        "extendedProperties": {"private": {PRIVATE_ETL_KEY: etl_id[:1024]}},
    }
    try:
        service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"[ETL] insert OK summary={summary!r}", flush=True)
        return True, False, None
    except Exception as exc:
        msg = str(exc)
        print(f"[ETL] insert FAIL etl_id={etl_id[:20]}... err={msg}", flush=True)
        _CAL_LOG.exception("Calendar event insert 실패 (etl_id=%s): %s", etl_id, exc)
        return False, False, msg


def sync_assignments_to_calendar(service, assignments: list[dict]) -> int:
    ok = 0
    for a in assignments:
        if insert_assignment_calendar_if_absent(service, a):
            ok += 1
    return ok
