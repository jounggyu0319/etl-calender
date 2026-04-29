"""
Moodle(eTL) 캘린더 «보내기» 구독 URL에서 ICS를 받아 Google 캘린더용 항목으로 변환.
파싱은 `icalendar` 라이브러리의 Calendar.from_ical → VEVENT 의 SUMMARY, DTSTART 등을 사용합니다.

activity_type 분류:
  - "assign"      : 과제·제출·보고서 등 직접 수행해야 하는 항목 (기존 코드와 일치)
  - "exam"        : 시험·퀴즈·평가
  - "presentation": 발표
  - "notice"      : 출결·안내·공지 등 — 기본적으로 동기화 제외
  - "ical_feed"   : 위 어디에도 해당 없는 일반 이벤트
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

ALLOWED_FEED_HOSTS = frozenset({"myetl.snu.ac.kr"})
_MAX_EVENTS = 400
_UA = "Mozilla/5.0 (compatible; eTL-Calendar-Sync/1.0)"

# ──────────────────────────────────────────────
# 이벤트 분류 키워드 (SUMMARY + DESCRIPTION 대상)
# 우선순위: notice 먼저 걸러낸 뒤 exam → presentation → assignment 순 판단
# ──────────────────────────────────────────────

# notice: 캘린더 등록 불필요한 공지/안내성 이벤트
_NOTICE_KW = re.compile(
    r"(출결|출석\s*현황|지정\s*좌석|좌석\s*배정|공지|안내|현황\s*공지"
    r"|수강\s*신청|휴강|보강|강의\s*계획|오리엔테이션|OT\b)",
    re.IGNORECASE,
)

# exam: 시험·평가
_EXAM_KW = re.compile(
    r"(시험|exam|test\b|중간\s*고사|기말\s*고사|중간\s*평가|기말\s*평가"
    r"|quiz|퀴즈|쪽지\s*시험|실기\s*시험|온라인\s*시험)",
    re.IGNORECASE,
)

# presentation: 발표
_PRESENTATION_KW = re.compile(
    r"(발표|presentation|프레젠테이션|발표\s*자료|발표\s*파일)",
    re.IGNORECASE,
)

# assignment: 과제·제출
_ASSIGNMENT_KW = re.compile(
    r"(과제|제출|assignment|homework|레포트|보고서|리포트|report\b"
    r"|실습|프로젝트\s*제출|프로젝트\s*보고)",
    re.IGNORECASE,
)


def classify_activity_type(summary: str, description: str = "") -> str:
    """
    SUMMARY + DESCRIPTION 텍스트를 분석해 activity_type 반환.

    반환값:
        "notice"       — 공지/안내성, 동기화 제외 권장
        "exam"         — 시험/퀴즈
        "presentation" — 발표
        "assign"       — 과제/제출 (canvas_sync, client_sync 등 기존 코드와 일치)
        "ical_feed"    — 분류 불가 일반 이벤트
    """
    text = f"{summary} {description}"

    # notice는 먼저 확인 — 다른 키워드가 섞여 있어도 공지면 공지로 처리
    if _NOTICE_KW.search(text):
        return "notice"
    if _EXAM_KW.search(text):
        return "exam"
    if _PRESENTATION_KW.search(text):
        return "presentation"
    if _ASSIGNMENT_KW.search(text):
        return "assign"
    return "ical_feed"


# ──────────────────────────────────────────────
# Google 캘린더 색상 ID 매핑
# https://developers.google.com/calendar/api/v3/reference/colors/list
# ──────────────────────────────────────────────
_COLOR_BY_TYPE: dict[str, str] = {
    "assign":       "6",   # Tangerine (주황) — canvas_sync/client_sync와 동일 키
    "exam":         "11",  # Tomato (빨강)
    "presentation": "9",   # Blueberry (남색)
    "notice":       "8",   # Graphite (회색) — 동기화 시 참고용
    "ical_feed":    "2",   # Sage (연두)
}


def get_color_id_for_type(activity_type: str) -> str:
    return _COLOR_BY_TYPE.get(activity_type, "2")


# ──────────────────────────────────────────────
# URL 유효성 검사 및 fetch
# ──────────────────────────────────────────────

def normalize_calendar_feed_url(raw: str) -> str:
    """webcal:// → https://, http:// → https://."""
    u = (raw or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[9:]
    elif u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    return u


def validate_moodle_calendar_feed_url(raw: str) -> str:
    u = normalize_calendar_feed_url(raw)
    if not u:
        return ""
    if not u.startswith("https://"):
        raise ValueError("캘린더 구독 URL은 https:// 또는 webcal:// 로 시작해야 합니다.")
    host = (urlparse(u).hostname or "").lower()
    if host not in ALLOWED_FEED_HOSTS:
        raise ValueError("허용된 주소는 myetl.snu.ac.kr 입니다.")
    p = (urlparse(u).path or "").lower()
    if (
        "export_execute.php" not in p
        and "export.php" not in p
        and "/ical" not in p
        and "export" not in p
        and "/feeds/" not in p
        and p.endswith(".ics") is False
    ):
        raise ValueError(
            "캘린더 «URL 주소 가져오기» 링크인지 확인해 주세요. "
            "경로에 보통 `calendar/export.php`, `calendar/export_execute.php`, `/feeds/`, 혹은 `.ics` 가 포함됩니다. "
            "캘린더 화면만(`.../calendar`) 복사하면 HTML이 내려와 동기화되지 않습니다."
        )
    return u


def _body_looks_like_html_login_page(text: str) -> bool:
    t = (text or "")[:8000].lower()
    if "<html" in t or "<!doctype html" in t:
        return True
    if "logintoken" in t and "login" in t:
        return True
    if "login/index.php" in t and "<form" in t:
        return True
    return False


def _final_url_allowed(url: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return h in ALLOWED_FEED_HOSTS


def _fetch_ics_once(url: str, timeout: int = 10) -> tuple[str, int]:
    u = validate_moodle_calendar_feed_url(url)
    logger.info("[iCal] 요청 URL: %s", u[:200])
    r = requests.get(
        u,
        timeout=timeout,
        headers={"User-Agent": _UA, "Accept": "text/calendar, application/ics, */*"},
        allow_redirects=True,
    )
    logger.info("[iCal] 응답 HTTP %s 최종 URL=%s", r.status_code, (r.url or "")[:200])
    print(f"[iCal] HTTP {r.status_code} ← {u[:120]}", flush=True)
    if r.status_code in (401, 403):
        raise ValueError(
            f"캘린더 구독 URL 요청이 거절되었습니다(HTTP {r.status_code}). "
            "myetl에서 «URL 주소 가져오기»로 받은 토큰이 포함된 링크인지, 만료되지 않았는지 확인해 주세요."
        )
    r.raise_for_status()
    if not _final_url_allowed(r.url):
        raise ValueError("리디렉션 결과 주소가 허용된 eTL 호스트가 아닙니다.")
    text = r.text or ""
    ct = (r.headers.get("Content-Type") or "").lower()
    if "text/html" in ct and "calendar" not in ct:
        raise ValueError(
            "응답이 HTML 페이지입니다(캘린더 ICS가 아님). "
            "로그인이 필요한 주소이거나, 캘린더 화면 URL을 잘못 복사했을 수 있습니다. "
            "myetl 캘린더 → 톱니바퀴 → «URL 주소 가져오기»의 전체 링크를 저장해 주세요."
        )
    if _body_looks_like_html_login_page(text):
        raise ValueError(
            "응답이 로그인 페이지(HTML)로 보입니다. "
            "구독 URL은 서버가 myetl에 로그인 없이 받을 수 있는 «보내기» 주소여야 합니다. "
            "토큰이 포함된 export.php 링크를 다시 복사해 주세요."
        )
    if "BEGIN:VCALENDAR" not in text.upper():
        raise ValueError(
            "응답에 ICS(`BEGIN:VCALENDAR`)가 없습니다. "
            "«URL 주소 가져오기» 링크가 맞는지, 중간에 잘리지 않았는지 확인해 주세요."
        )
    return text, r.status_code


def _alternate_feed_urls(url: str) -> list[str]:
    """myetl 단일 호스트. `export.php` ↔ `export_execute.php` 를 순서대로 시도."""
    u = validate_moodle_calendar_feed_url(url)
    pr = urlparse(u)
    path = pr.path or ""
    out: list[str] = [u]
    pl = path.lower()
    if "export_execute.php" in pl:
        alt_path = re.sub(r"export_execute\.php", "export.php", path, count=1, flags=re.IGNORECASE)
        if alt_path != path:
            out.append(urlunparse((pr.scheme, pr.netloc, alt_path, pr.params, pr.query, pr.fragment)))
    elif re.search(r"export\.php", pl) and "export_execute" not in pl:
        alt_path = re.sub(r"export\.php", "export_execute.php", path, count=1, flags=re.IGNORECASE)
        if alt_path != path:
            out.append(urlunparse((pr.scheme, pr.netloc, alt_path, pr.params, pr.query, pr.fragment)))
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def fetch_moodle_calendar_ics(url: str, timeout: int = 10) -> str:
    last_err: Exception | None = None
    for candidate in _alternate_feed_urls(url):
        try:
            text, _code = _fetch_ics_once(candidate, timeout=timeout)
            return text
        except Exception as e:
            logger.warning("[iCal] URL 실패 (%s): %s", candidate[:120], e)
            last_err = e
    if last_err:
        raise last_err
    raise ValueError("iCal URL을 처리할 수 없습니다.")


# ──────────────────────────────────────────────
# ICS 파싱
# ──────────────────────────────────────────────

def _decode_ical_text(val) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def _dtstart_date_kst(component) -> date | None:
    """DTSTART를 Asia/Seoul 기준 달력 날짜로 변환. 없거나 파싱 불가면 None."""
    dt = component.get("dtstart")
    if dt is None:
        return None
    try:
        v = dt.dt
    except Exception:
        return None
    # datetime은 date의 서브클래스이므로 datetime을 먼저 검사
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(_KST).date()
    if isinstance(v, date):
        return v
    return None


def _dtstart_as_deadline_str(component) -> str | None:
    dt = component.get("dtstart")
    if dt is None:
        return None
    try:
        v = dt.dt
    except Exception:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return None


def _first_url_in_text(blob: str) -> str:
    m = re.search(r"https?://[^\s<>\"']+", blob or "")
    return m.group(0) if m else ""


def ical_to_assignment_items(
    ics_text: str,
    include_notices: bool = False,
) -> list[dict]:
    """
    VEVENT → Google 캘린더용 항목 리스트 변환.

    Args:
        ics_text: ICS 원문
        include_notices: True이면 activity_type="notice" 항목도 포함.
                         기본값 False — 공지/안내 이벤트는 제외.

    Returns:
        각 항목 dict:
            id, title, subject, url, activity_type, color_id, deadline
            (description_extra: 있을 때만)
    """
    try:
        cal = Calendar.from_ical(ics_text)
    except Exception as e:
        logger.error("[iCal] Calendar.from_ical 실패: %s", e)
        raise ValueError(f"ICS 파싱 실패: {e}") from e

    vevent_count = 0
    skipped_rrule = 0
    skipped_no_dtstart = 0
    skipped_before_today = 0
    skipped_notice = 0
    out: list[dict] = []
    today_kst = datetime.now(_KST).date()

    for ev in cal.walk():
        if ev.name != "VEVENT":
            continue
        vevent_count += 1
        if ev.get("rrule"):
            skipped_rrule += 1
            continue

        summary = _decode_ical_text(ev.get("summary")).strip() or "(제목 없음)"
        desc = _decode_ical_text(ev.get("description"))
        loc = _decode_ical_text(ev.get("location"))
        url_field = _decode_ical_text(ev.get("url")).strip()
        link = url_field or _first_url_in_text(desc) or ""

        start_date_kst = _dtstart_date_kst(ev)
        if start_date_kst is None:
            skipped_no_dtstart += 1
            continue
        if start_date_kst < today_kst:
            skipped_before_today += 1
            continue

        deadline = _dtstart_as_deadline_str(ev)
        if not deadline:
            skipped_no_dtstart += 1
            continue

        # ── 이벤트 유형 분류 ──
        activity_type = classify_activity_type(summary, desc)

        if activity_type == "notice" and not include_notices:
            skipped_notice += 1
            logger.debug("[iCal] 공지 제외: %r", summary[:80])
            continue

        dtend_raw = ev.get("dtend")
        dtend_str: str | None = None
        if dtend_raw is not None:
            try:
                v_end = dtend_raw.dt
                if isinstance(v_end, datetime):
                    if v_end.tzinfo is None:
                        v_end = v_end.replace(tzinfo=timezone.utc)
                    dtend_str = v_end.isoformat()
                elif isinstance(v_end, date):
                    dtend_str = v_end.isoformat()
            except Exception:
                dtend_str = None

        uid_raw = ev.get("uid")
        uid = _decode_ical_text(uid_raw).strip()
        if uid:
            eid = f"ical:{uid}"
        else:
            h = hashlib.sha256(f"{summary}|{deadline}|{link}".encode("utf-8")).hexdigest()[:40]
            eid = f"ical:nouid:{h}"

        subj = "eTL 캘린더"
        if loc:
            subj = f"eTL 캘린더 · {loc[:60]}"

        row: dict = {
            "id": eid,
            "title": summary[:500],
            "subject": subj,
            "url": link[:2000],
            "activity_type": activity_type,
            "color_id": get_color_id_for_type(activity_type),
            "deadline": deadline,
        }
        desc_plain = str(desc or "").strip()
        if desc_plain:
            row["description_extra"] = desc_plain[:7000]
        out.append(row)

        if len(out) <= 3:
            logger.info(
                "[iCal] 샘플 이벤트: SUMMARY=%r TYPE=%r DTSTART=%r DTEND=%r DESC앞50자=%r",
                summary[:80],
                activity_type,
                deadline,
                dtend_str,
                (desc or "")[:50],
            )
            print(
                f"[iCal] 샘플 #{len(out)} SUMMARY={summary[:60]!r} TYPE={activity_type!r} "
                f"DTSTART={deadline!r} DTEND={dtend_str!r}",
                flush=True,
            )

        if len(out) >= _MAX_EVENTS:
            break

    logger.info(
        "[iCal] 파싱: VEVENT 총 %d개 → 사용 %d개 "
        "(RRULE 제외 %d, DTSTART 없음 %d, 오늘 이전(KST) 제외 %d, 공지 제외 %d)",
        vevent_count,
        len(out),
        skipped_rrule,
        skipped_no_dtstart,
        skipped_before_today,
        skipped_notice,
    )
    print(
        f"[iCal] 파싱된 이벤트 수: {len(out)} "
        f"(VEVENT {vevent_count}개 중 / 과거(KST) {skipped_before_today}개 제외 / 공지 {skipped_notice}개 제외)",
        flush=True,
    )
    if not out and (ics_text or "").strip():
        logger.warning("[iCal] 변환된 일정 0건 — 원본 앞 500자:\n%s", (ics_text or "")[:500])
    return out
