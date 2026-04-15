"""브라우저·데스크톱 클라이언트가 myetl 세션으로 수집한 과제 목록을 Google 캘린더에 반영.

Google 일정 중복 방지는 `calendar_service.insert_assignment_calendar_if_absent`가
`extendedProperties.private.etl_id` 로 Google Calendar API를 조회해 판단합니다.
일정 제목·메모 형식은 `app.services.calendar_service`의 `format_calendar_event_*` 에서 통일합니다.
`activity_type` 이 `exam` 인 항목(시험 공지)은 `posted_at`·`description_extra` 를 넘길 수 있습니다.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

_LOG = logging.getLogger(__name__)

from app.config import Settings
from app.models import User
from app.schemas import ClientSyncItem, SyncResult
from app.security import decrypt_text, encrypt_text
from calendar_service import (
    ensure_calendar_service,
    insert_assignment_calendar_if_absent,
    probe_calendar_access,
)
from app.services.gemini_classifier import is_exam_schedule_announcement


def import_from_client(
    db: Session,
    user: User,
    settings: Settings,
    items: list[ClientSyncItem],
) -> SyncResult:
    google_json = decrypt_text(user.google_creds_enc, settings)
    if not google_json:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="Google Calendar 연동을 먼저 완료해 주세요.",
            login_ok=True,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
        )

    fresh: list[dict] = []
    for row in items:
        d = row.model_dump()
        aid = str(d.get("id") or "").strip()
        if not aid:
            continue
        row: dict = {
            "id": aid,
            "title": (d.get("title") or "").strip() or aid,
            "subject": (d.get("subject") or "").strip() or "eTL",
            "url": (d.get("url") or "").strip() or aid,
            "activity_type": (d.get("activity_type") or "assign").strip() or "assign",
            "deadline": (d.get("deadline") or "").strip(),
        }
        pa = (d.get("posted_at") or "").strip()
        if pa:
            row["posted_at"] = pa
        xtra = (d.get("description_extra") or "").strip()
        if xtra:
            row["description_extra"] = xtra
        fresh.append(row)

    assign_n = sum(1 for x in fresh if x.get("activity_type") == "assign")
    quiz_n = sum(1 for x in fresh if x.get("activity_type") == "quiz")
    ann_n = sum(
        1
        for x in fresh
        if x.get("activity_type") in ("announcement_midterm", "announcement_final")
    )
    course_subjects = {x["subject"] for x in fresh}

    if not fresh:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="전송된 항목이 없습니다.",
            login_ok=True,
            courses_found=len(course_subjects),
            assign_links_found=assign_n,
            quiz_links_found=quiz_n,
            announcement_keyword_hits=ann_n,
            login_note="클라이언트 동기화",
        )

    try:
        service, fresh_google_json = ensure_calendar_service(google_json)
    except Exception as exc:
        err_msg = str(exc)
        print(f"[ETL] ensure_calendar_service 실패: {err_msg}", flush=True)
        return SyncResult(
            new_assignments=len(fresh),
            calendar_events_created=0,
            ics_events_created=0,
            message=f"Google Calendar 서비스 초기화 오류: {err_msg}",
            login_ok=True,
            courses_found=len(course_subjects),
            assign_links_found=assign_n,
            quiz_links_found=quiz_n,
            announcement_keyword_hits=ann_n,
            login_note="클라이언트 동기화",
        )

    # Verify Calendar API access before attempting inserts
    probe_err = probe_calendar_access(service)
    if probe_err:
        return SyncResult(
            new_assignments=len(fresh),
            calendar_events_created=0,
            ics_events_created=0,
            message=f"Google Calendar 접근 오류 (재연동 필요): {probe_err}",
            login_ok=True,
            courses_found=len(course_subjects),
            assign_links_found=assign_n,
            quiz_links_found=quiz_n,
            announcement_keyword_hits=ann_n,
            login_note="클라이언트 동기화",
        )

    gemini_key = settings.gemini_api_key
    created = 0
    skipped = 0
    first_err: str | None = None
    for a in fresh:
        # exam 타입 공지는 Gemini로 2차 검증 (자료·발표 공지 오인 방지)
        if a.get("activity_type") == "exam":
            if not is_exam_schedule_announcement(
                a.get("title", ""),
                a.get("description_extra", ""),
                gemini_key,
            ):
                _LOG.info("Gemini: exam 아님, 스킵 → %s", a.get("title", "")[:50])
                continue

        inserted, existed, err = insert_assignment_calendar_if_absent(service, a)
        if inserted:
            created += 1
        elif existed:
            skipped += 1
        elif err and first_err is None:
            first_err = err

    if fresh_google_json != google_json:
        user.google_creds_enc = encrypt_text(fresh_google_json, settings)
    db.add(user)
    db.commit()

    print(f"[ETL] sync done: created={created} skipped={skipped} err={first_err}", flush=True)

    if first_err:
        msg = f"Google Calendar 추가 오류: {first_err}"
    elif skipped == len(fresh):
        msg = "모든 항목이 이미 캘린더에 있습니다. (중복 방지)"
    elif created < len(fresh) - skipped:
        msg = "일부 항목을 추가하지 못했습니다. Google 재연동을 시도해 주세요."
    else:
        msg = None

    return SyncResult(
        new_assignments=len(fresh),
        calendar_events_created=created,
        ics_events_created=0,
        message=msg,
        login_ok=True,
        courses_found=len(course_subjects),
        assign_links_found=assign_n,
        quiz_links_found=quiz_n,
        announcement_keyword_hits=ann_n,
        login_note="클라이언트 동기화",
    )
