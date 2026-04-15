"""브라우저·데스크톱 클라이언트가 myetl 세션으로 수집한 과제 목록을 Google 캘린더에 반영.

Google 일정 중복 방지는 `calendar_service.insert_assignment_calendar_if_absent`가
`extendedProperties.private.etl_id` 로 Google Calendar API를 조회해 판단합니다.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.schemas import ClientSyncItem, SyncResult
from app.security import decrypt_text, encrypt_text
from calendar_service import ensure_calendar_service, insert_assignment_calendar_if_absent


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
        fresh.append(
            {
                "id": aid,
                "title": (d.get("title") or "").strip() or aid,
                "subject": (d.get("subject") or "").strip() or "eTL",
                "url": (d.get("url") or "").strip() or aid,
                "activity_type": (d.get("activity_type") or "assign").strip() or "assign",
                "deadline": (d.get("deadline") or "").strip(),
            }
        )

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

    service, fresh_google_json = ensure_calendar_service(google_json)
    created = 0
    for a in fresh:
        if insert_assignment_calendar_if_absent(service, a):
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
            "Google Calendar에 추가되지 않은 항목이 있습니다. 토큰·쿼터를 확인해 주세요."
            if partial
            else None
        ),
        login_ok=True,
        courses_found=len(course_subjects),
        assign_links_found=assign_n,
        quiz_links_found=quiz_n,
        announcement_keyword_hits=ann_n,
        login_note="클라이언트 동기화",
    )
