"""서버 저장 Canvas 액세스 토큰으로 myetl REST API 수집 후 Google Calendar 반영."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.schemas import SyncResult
from app.security import decrypt_text, encrypt_text
from app.snu_academic_calendar import due_at_in_active_window
from calendar_service import ensure_calendar_service, insert_assignment_calendar_if_absent

logger = logging.getLogger(__name__)

MYETL_CANVAS_BASE = "https://myetl.snu.ac.kr"


def _canvas_html_to_plain(html: Any, limit: int = 6000) -> str:
    if html is None or not isinstance(html, str):
        return ""
    t = re.sub(r"<[^>]+>", " ", html)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        m = re.search(r"<([^>]+)>;\s*rel=\"next\"", part)
        if m:
            return m.group(1).strip()
    return None


def _fetch_all_pages(first_url: str, headers: dict[str, str], timeout: int = 45) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    url: str | None = first_url
    while url:
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            logger.warning("[canvas_sync] GET 실패: %s", e)
            break
        if r.status_code == 404:
            break
        r.raise_for_status()
        chunk = r.json()
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        url = _parse_next_link(r.headers.get("Link"))
    return rows


def _course_label(c: dict[str, Any]) -> str:
    code = str(c.get("course_code") or "").strip()
    name = str(c.get("name") or "").strip()
    if code and name:
        return f"{code} {name}"
    return name or code or f"Course {c.get('id')}"


def run_canvas_server_sync(db: Session, user: User, settings: Settings) -> SyncResult:
    google_json = decrypt_text(user.google_creds_enc, settings)
    if not google_json:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="Google Calendar 연동을 먼저 완료해 주세요.",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
            canvas_server_context=True,
        )

    tok_enc = user.canvas_token_enc
    if not tok_enc:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="Canvas API 토큰을 먼저 저장해 주세요. (myetl 프로필 → 새 액세스 토큰)",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
            canvas_server_context=True,
        )

    token = (decrypt_text(tok_enc, settings) or "").strip()
    if not token:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="Canvas API 토큰이 비어 있습니다. 다시 저장해 주세요.",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
            canvas_server_context=True,
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        courses = _fetch_all_pages(
            f"{MYETL_CANVAS_BASE}/api/v1/courses?enrollment_state=active&per_page=100",
            headers,
        )
    except requests.RequestException as e:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message=f"Canvas 강의 목록을 불러오지 못했습니다: {e}",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
            canvas_server_context=True,
        )

    fresh: list[dict[str, Any]] = []
    assign_n = quiz_n = 0

    for c in courses[:60]:
        raw_id = c.get("id")
        if raw_id is None:
            continue
        cid = int(raw_id)
        subj = _course_label(c)
        try:
            assignments = _fetch_all_pages(
                f"{MYETL_CANVAS_BASE}/api/v1/courses/{cid}/assignments?per_page=100",
                headers,
            )
        except requests.RequestException:
            continue
        try:
            quizzes = _fetch_all_pages(
                f"{MYETL_CANVAS_BASE}/api/v1/courses/{cid}/quizzes?per_page=100",
                headers,
            )
        except requests.RequestException:
            quizzes = []

        included_assign_ids: set[int] = set()
        for a in assignments:
            due = a.get("due_at")
            if not due_at_in_active_window(due if isinstance(due, str) else None):
                continue
            aid_raw = a.get("id")
            if aid_raw is None:
                continue
            aid = int(aid_raw)
            included_assign_ids.add(aid)
            eid = f"canvas-{cid}-assign-{aid}"
            desc_plain = _canvas_html_to_plain(a.get("description"))
            fresh.append(
                {
                    "id": eid,
                    "title": str(a.get("name") or "과제").strip()[:500] or "과제",
                    "subject": subj[:256],
                    "url": str(a.get("html_url") or "").strip()
                    or f"{MYETL_CANVAS_BASE}/courses/{cid}/assignments/{aid}",
                    "activity_type": "assign",
                    "deadline": str(due).strip(),
                    **({"description_extra": desc_plain} if desc_plain else {}),
                }
            )
            assign_n += 1

        for q in quizzes:
            qaid = q.get("assignment_id")
            if qaid is not None and int(qaid) in included_assign_ids:
                continue
            due = q.get("due_at")
            if not due_at_in_active_window(due if isinstance(due, str) else None):
                continue
            qid_raw = q.get("id")
            if qid_raw is None:
                continue
            qid = int(qid_raw)
            eid = f"canvas-{cid}-quiz-{qid}"
            qdesc_plain = _canvas_html_to_plain(q.get("description"))
            fresh.append(
                {
                    "id": eid,
                    "title": str(q.get("title") or "퀴즈").strip()[:500] or "퀴즈",
                    "subject": subj[:256],
                    "url": str(q.get("html_url") or "").strip()
                    or f"{MYETL_CANVAS_BASE}/courses/{cid}/quizzes/{qid}",
                    "activity_type": "quiz",
                    "deadline": str(due).strip(),
                    **({"description_extra": qdesc_plain} if qdesc_plain else {}),
                }
            )
            quiz_n += 1

    if not fresh:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="현재 학기 기준으로 새로 반영할 과제·퀴즈가 없습니다. "
            "(기간 필터에 걸리지 않았거나 Google 캘린더에 동일 일정이 이미 있을 수 있습니다.)",
            login_ok=True,
            courses_found=len(courses),
            assign_links_found=assign_n,
            quiz_links_found=quiz_n,
            announcement_keyword_hits=0,
            login_note="Canvas API 서버 동기화",
            course_list_scanned=True,
            canvas_server_context=True,
        )

    service, fresh_google_json = ensure_calendar_service(google_json)
    created = 0
    for it in fresh:
        if insert_assignment_calendar_if_absent(service, it):
            created += 1

    if fresh_google_json != google_json:
        user.google_creds_enc = encrypt_text(fresh_google_json, settings)
    db.add(user)
    db.commit()

    partial = created < len(fresh)
    return SyncResult(
        new_assignments=len(fresh),
        calendar_events_created=created,
        ics_events_created=0,
        message=(
            "일부 일정만 캘린더에 추가되었습니다. 토큰·쿼터·권한을 확인해 주세요."
            if partial
            else None
        ),
        login_ok=True,
        courses_found=len(courses),
        assign_links_found=assign_n,
        quiz_links_found=quiz_n,
        announcement_keyword_hits=0,
        login_note="Canvas API 서버 동기화",
        course_list_scanned=True,
        canvas_server_context=True,
    )
