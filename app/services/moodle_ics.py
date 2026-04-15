"""
Moodle(eTL) 캘린더 «보내기» 구독 URL에서 ICS를 받아 Google 캘린더용 항목으로 변환.
파싱은 `icalendar` 라이브러리의 Calendar.from_ical → VEVENT 의 SUMMARY, DTSTART 등을 사용합니다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse, urlunparse

import requests
from icalendar import Calendar

logger = logging.getLogger(__name__)

ALLOWED_FEED_HOSTS = frozenset({"myetl.snu.ac.kr"})
_MAX_EVENTS = 400
_UA = "Mozilla/5.0 (compatible; eTL-Calendar-Sync/1.0)"


def normalize_calendar_feed_url(raw: str) -> str:
    """webcal:// → https://, http:// → https://."""
    u = (raw or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[9:]
    elif u.startswith("http://"):
        u = "https://" + u[len("http://") :]
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


def _decode_ical_text(val) -> str:
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


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


def ical_to_assignment_items(ics_text: str) -> list[dict]:
    """VEVENT → add_assignment_to_calendar (icalendar: SUMMARY, DTSTART, DESCRIPTION, URL 등)."""
    try:
        cal = Calendar.from_ical(ics_text)
    except Exception as e:
        logger.error("[iCal] Calendar.from_ical 실패: %s", e)
        raise ValueError(f"ICS 파싱 실패: {e}") from e

    vevent_count = 0
    skipped_rrule = 0
    skipped_no_dtstart = 0
    out: list[dict] = []
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

        deadline = _dtstart_as_deadline_str(ev)
        if not deadline:
            skipped_no_dtstart += 1
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
            "activity_type": "ical_feed",
            "deadline": deadline,
        }
        desc_plain = str(desc or "").strip()
        if desc_plain:
            row["description_extra"] = desc_plain[:7000]
        out.append(row)
        if len(out) <= 3:
            logger.info(
                "[iCal] 샘플 이벤트(icalendar): SUMMARY=%r DTSTART=%r DTEND=%r DESCRIPTION_앞50자=%r",
                summary[:80],
                deadline,
                dtend_str,
                (desc or "")[:50],
            )
            print(
                f"[iCal] 샘플 #{len(out)} SUMMARY={summary[:60]!r} DTSTART={deadline!r} DTEND={dtend_str!r}",
                flush=True,
            )
        if len(out) >= _MAX_EVENTS:
            break

    logger.info(
        "[iCal] 파싱: VEVENT 총 %d개 → 사용 %d개 (RRULE 제외 %d, DTSTART 없음 %d)",
        vevent_count,
        len(out),
        skipped_rrule,
        skipped_no_dtstart,
    )
    print(
        f"[iCal] 파싱된 이벤트(일정) 개수: {len(out)} (VEVENT {vevent_count}개 중)",
        flush=True,
    )
    if not out and (ics_text or "").strip():
        logger.warning("[iCal] 변환된 일정 0건 — 원본 앞 500자:\n%s", (ics_text or "")[:500])
    return out
