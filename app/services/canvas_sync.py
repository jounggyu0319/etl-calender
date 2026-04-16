"""서버 저장 Canvas 액세스 토큰으로 myetl REST API 수집 후 Google Calendar 반영."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.schemas import SyncResult
from app.security import decrypt_text, encrypt_text
from app.services.calendar_service import announcement_title_matches_exam_keywords, announcement_has_deadline_hint
from app.services.gemini_classifier import classify_exam_announcement
from app.services.sync_progress import clear_progress, set_progress
from app.services.sync_log import log_sync_item, prune_sync_logs
from app.snu_academic_calendar import (
    SEOUL,
    due_at_in_active_window,
    pick_due_date_filter_window,
    posted_at_in_active_window,
)
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


def _canvas_json(r: requests.Response) -> Any:
    """Canvas API는 CSRF 방지용 while(1); prefix를 붙임 — 제거 후 파싱."""
    text = r.text
    if text.startswith("while(1);"):
        text = text[9:]
    return json.loads(text)


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
        try:
            chunk = _canvas_json(r)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[canvas_sync] JSON 파싱 실패: %s", e)
            break
        if not isinstance(chunk, list):
            break
        rows.extend(chunk)
        url = _parse_next_link(r.headers.get("Link"))
    return rows


def _is_course_in_current_semester(c: dict[str, Any]) -> bool:
    """확장 프로그램 isCourseInCurrentSemester()와 동일 로직.

    1순위: term.start_at / end_at 으로 현재 학기 창과 겹치는지 확인
    2순위: 강의명에 "YYYY-N" 코드 포함 여부
    """
    win_start, win_end = pick_due_date_filter_window()
    term = c.get("term") or {}
    ts_raw = term.get("start_at")
    te_raw = term.get("end_at")
    if ts_raw and te_raw:
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).astimezone(SEOUL)
            te = datetime.fromisoformat(str(te_raw).replace("Z", "+00:00")).astimezone(SEOUL)
            return ts <= win_end and te >= win_start
        except ValueError:
            pass
    # fallback: "YYYY-N" 코드 포함 여부
    now = win_start  # 현재 학기 시작 기준으로 학기 코드 계산
    m = now.month
    y = now.year
    if 3 <= m <= 8:
        sem_code = f"{y}-1"
    elif m >= 9:
        sem_code = f"{y}-2"
    else:
        sem_code = f"{y - 1}-2"
    name = str(c.get("name") or c.get("course_code") or "").strip()
    return name.startswith(sem_code) or f"[{sem_code}" in name


def _course_label(c: dict[str, Any]) -> str:
    code = str(c.get("course_code") or "").strip()
    name = str(c.get("name") or "").strip()
    if code and name:
        # course_code와 name이 동일하거나 한쪽이 다른 쪽을 포함하면 중복 방지
        if code == name or code in name or name in code:
            return name or code
        return f"{code} {name}"
    return name or code or f"Course {c.get('id')}"


def run_canvas_server_sync(db: Session, user: User, settings: Settings) -> SyncResult:  # noqa: C901
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

    uid = user.id
    clear_progress(uid)
    set_progress(uid, running=True, phase="강의 목록 불러오는 중…", course_index=0, course_total=0, course_name="")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        courses = _fetch_all_pages(
            f"{MYETL_CANVAS_BASE}/api/v1/courses?enrollment_state=active&include[]=term&per_page=100",
            headers,
        )
    except requests.RequestException as e:
        clear_progress(uid)
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
    assign_n = quiz_n = ann_n = 0

    # 현재 학기 강의만 필터링 (확장 프로그램 isCourseInCurrentSemester와 동일)
    semester_courses = [c for c in courses if _is_course_in_current_semester(c)]
    course_list = (semester_courses if semester_courses else courses)[:60]
    logger.info("[canvas_sync] 전체 강의 %d개 → 학기 필터 후 %d개", len(courses), len(course_list))
    total = len(course_list)

    for idx, c in enumerate(course_list, 1):
        raw_id = c.get("id")
        if raw_id is None:
            continue
        cid = int(raw_id)
        subj = _course_label(c)
        set_progress(uid, running=True, phase="스캔 중", course_index=idx, course_total=total, course_name=subj)
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
                    "color_id": user.assign_color_id or "9",
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
                    "color_id": user.assign_color_id or "9",
                    "deadline": str(due).strip(),
                    **({"description_extra": qdesc_plain} if qdesc_plain else {}),
                }
            )
            quiz_n += 1

        try:
            topics = _fetch_all_pages(
                f"{MYETL_CANVAS_BASE}/api/v1/courses/{cid}/discussion_topics"
                f"?only_announcements=true&per_page=50",
                headers,
            )
        except requests.RequestException:
            topics = []

        for topic in topics:
            title = str(topic.get("title") or "").strip()
            if not title:
                continue
            posted = topic.get("posted_at") or topic.get("delayed_post_at")
            if not posted_at_in_active_window(posted if isinstance(posted, str) else None):
                continue
            tid_raw = topic.get("id")
            if tid_raw is None:
                continue
            tid = int(tid_raw)

            # 공지 본문 추출 (pre-filter + Claude 분류에 필요)
            body_html = str(topic.get("message") or "")
            body_text = _canvas_html_to_plain(body_html, limit=7900)

            # pre-filter: 시험 키워드 OR 마감 힌트 있는 공지만 Claude로 전달
            is_exam_kw = announcement_title_matches_exam_keywords(title)
            is_deadline_hint = announcement_has_deadline_hint(title, body_text)
            if not is_exam_kw and not is_deadline_hint:
                continue

            # Claude 2차 분류 — 시험/마감 판단 + 날짜·시각·장소 추출
            is_exam, exam_date, exam_location, exam_time, has_deadline, deadline_date = classify_exam_announcement(
                title, body_text, settings.anthropic_api_key
            )

            eid = f"canvas-{cid}-announce-{tid}"
            html_url = str(topic.get("html_url") or "").strip()
            url = html_url or f"{MYETL_CANVAS_BASE}/courses/{cid}/discussion_topics/{tid}"

            if is_exam:
                deadline_val = exam_date if exam_date else body_text[:2000]
                logger.info("[canvas_sync] 시험 공지 [%s] → date=%s time=%s loc=%s", title[:40], exam_date, exam_time, exam_location)
                item: dict = {
                    "id": eid,
                    "title": title[:500],
                    "subject": subj[:256],
                    "url": url,
                    "activity_type": "exam",
                    "color_id": user.exam_color_id or "11",
                    "deadline": deadline_val,
                    "posted_at": str(posted).strip(),
                    "description_extra": (body_text or title)[:7900],
                }
                if exam_location:
                    item["exam_location"] = exam_location[:200]
                if exam_time:
                    item["exam_time"] = exam_time[:20]
                fresh.append(item)
                ann_n += 1
            elif has_deadline and deadline_date:
                logger.info("[canvas_sync] 마감 공지 [%s] → deadline_date=%s", title[:40], deadline_date)
                item = {
                    "id": f"{eid}-dl",
                    "title": title[:500],
                    "subject": subj[:256],
                    "url": url,
                    "activity_type": "announcement_deadline",
                    "color_id": user.assign_color_id or "9",
                    "deadline": deadline_date,
                    "posted_at": str(posted).strip(),
                    "description_extra": (body_text or title)[:7900],
                }
                fresh.append(item)
                ann_n += 1
            else:
                logger.info("[canvas_sync] Claude: 시험/마감 아님, 스킵 → %s", title[:50])

    set_progress(uid, running=True, phase="캘린더에 반영 중…", course_index=total, course_total=total, course_name="")

    if not fresh:
        clear_progress(uid)
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="현재 학기 기준으로 새로 반영할 과제·퀴즈·시험 공지가 없습니다. "
            "(기간 필터에 걸리지 않았거나 Google 캘린더에 동일 일정이 이미 있을 수 있습니다.)",
            login_ok=True,
            courses_found=len(courses),
            assign_links_found=assign_n,
            quiz_links_found=quiz_n,
            announcement_keyword_hits=ann_n,
            login_note="Canvas API 서버 동기화",
            course_list_scanned=True,
            canvas_server_context=True,
        )

    service, fresh_google_json = ensure_calendar_service(google_json)
    created = 0
    skipped = 0
    erred = 0
    first_err: str | None = None
    for it in fresh:
        inserted, existed, err = insert_assignment_calendar_if_absent(service, it)
        if inserted:
            created += 1
            log_sync_item(db, uid, it)
        elif existed:
            skipped += 1
        elif err:
            erred += 1
            if first_err is None:
                first_err = err

    if fresh_google_json != google_json:
        user.google_creds_enc = encrypt_text(fresh_google_json, settings)
    db.add(user)
    # 원인: 항목별 prune으로 count/select/delete가 반복되어 N+1 쿼리가 발생함.
    prune_sync_logs(db, uid)
    try:
        db.commit()
    except Exception as exc:
        # 원인: 로그 flush 이후 commit 실패 시 세션 rollback 없이 남아 후속 트랜잭션을 오염시킴.
        db.rollback()
        clear_progress(uid)
        return SyncResult(
            new_assignments=len(fresh),
            calendar_events_created=created,
            ics_events_created=0,
            message=f"동기화 결과 저장(DB commit)에 실패했습니다: {exc}",
            login_ok=True,
            courses_found=len(courses),
            assign_links_found=assign_n,
            quiz_links_found=quiz_n,
            announcement_keyword_hits=ann_n,
            login_note="Canvas API 서버 동기화",
            course_list_scanned=True,
            canvas_server_context=True,
        )
    clear_progress(uid)

    if first_err:
        msg = f"Google Calendar 추가 오류: {first_err}"
    elif skipped == len(fresh):
        msg = None  # 전부 이미 존재 → 정상
    elif erred > 0:
        msg = "일부 일정을 캘린더에 추가하지 못했습니다. 토큰·쿼터·권한을 확인해 주세요."
    else:
        msg = None

    return SyncResult(
        new_assignments=len(fresh),
        calendar_events_created=created,
        ics_events_created=0,
        message=msg,
        login_ok=True,
        courses_found=len(courses),
        assign_links_found=assign_n,
        quiz_links_found=quiz_n,
        announcement_keyword_hits=ann_n,
        login_note="Canvas API 서버 동기화",
        course_list_scanned=True,
        canvas_server_context=True,
    )
