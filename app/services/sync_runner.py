import time

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.schemas import SyncResult
from app.security import decrypt_text, encrypt_text
from app.services.etl_session_holder import peek as etl_driver_peek
from app.services.etl_session_holder import remove as etl_driver_remove
from app.services.etl_session_holder import store as etl_driver_store
from app.etl_types import CollectResult
from app.services.moodle_ics import fetch_moodle_calendar_ics, ical_to_assignment_items
from app.services.sync_progress import clear_progress, set_progress
from calendar_service import ensure_calendar_service, insert_assignment_calendar_if_absent, sync_assignments_to_calendar


def _etl_scraper():
    """Selenium은 requirements-dev.txt 에만 포함. 프로덕션 이미지에서는 이 모듈을 import 하지 않음."""
    try:
        import etl_scraper as m
    except ImportError as e:
        raise RuntimeError(
            "Selenium(eTL 스크래퍼)가 설치되어 있지 않습니다. 로컬에서 브라우저 동기화를 쓰려면 "
            "`pip install -r requirements-dev.txt` 로 selenium 을 설치하세요."
        ) from e
    return m


def _diag(
    report: CollectResult,
    *,
    new_count: int,
    created: int,
    ics_created: int = 0,
    message: str | None,
    etl_awaiting_user: bool = False,
    course_list_scanned: bool = False,
    ical_feed_configured: bool = False,
    ical_sync_attempted: bool = False,
    ical_sync_ok: bool | None = None,
    ical_ui_context: bool = False,
) -> SyncResult:
    return SyncResult(
        new_assignments=new_count,
        calendar_events_created=created,
        ics_events_created=ics_created,
        message=message,
        login_ok=report.login_ok,
        courses_found=report.courses_found,
        assign_links_found=report.assign_links_found,
        quiz_links_found=report.quiz_links_found,
        announcement_keyword_hits=report.announcement_keyword_hits,
        login_note=report.login_note,
        etl_awaiting_user=etl_awaiting_user,
        course_list_scanned=course_list_scanned,
        ical_feed_configured=ical_feed_configured,
        ical_sync_attempted=ical_sync_attempted,
        ical_sync_ok=ical_sync_ok,
        ical_ui_context=ical_ui_context,
    )


def _ical_merge_only(
    user: User,
    settings: Settings,
    google_json: str,
) -> tuple[str, bool, int, str | None, bool, bool | None]:
    """iCal 구독만 반영. google_json(토큰 갱신 시)만 갱신해 반환.

    반환 마지막 두 값: (ical_feed_configured, ical_sync_ok)
    - ical_feed_configured: 구독 URL이 비어 있지 않음
    - ical_sync_ok: URL이 있어 fetch 시도함 → 예외 없이 끝나면 True, 예외면 False, URL 없으면 None
    """
    ics_created_total = 0
    ics_err: str | None = None
    google_changed = False
    feed_plain = ""
    if user.moodle_calendar_feed_enc:
        feed_plain = (decrypt_text(user.moodle_calendar_feed_enc, settings) or "").strip()
    if not feed_plain:
        return google_json, google_changed, ics_created_total, ics_err, False, None
    ical_ok: bool | None = True
    try:
        ics_body = fetch_moodle_calendar_ics(feed_plain)
        items = ical_to_assignment_items(ics_body)
        if not items and "BEGIN:VEVENT" in (ics_body or "").upper():
            ics_err = (
                "구독 ICS에 이벤트는 있으나, 반복 일정(RRULE)이거나 시작일시(DTSTART)가 없어 "
                "이 앱에서 Google로 넣을 항목이 없습니다."
            )
        if items:
            service, gj = ensure_calendar_service(google_json)
            for it in items:
                inserted, _, _ = insert_assignment_calendar_if_absent(service, it)
                if inserted:
                    ics_created_total += 1
            if ics_created_total == 0:
                hint = (
                    "Google Calendar에 새 일정을 추가하지 못했습니다. "
                    "OAuth 연결(캘린더 쓰기 권한)·API 오류·이미 동일 etl_id 일정이 있을 수 있습니다."
                )
                ics_err = f"{ics_err} {hint}" if ics_err else hint
            if gj != google_json:
                google_json = gj
                google_changed = True
    except Exception as e:
        ics_err = str(e)
        ical_ok = False
    return google_json, google_changed, ics_created_total, ics_err, True, ical_ok


def _commit_user_google_maybe(
    db: Session,
    user: User,
    settings: Settings,
    google_json: str,
    google_changed: bool,
) -> None:
    if google_changed:
        user.google_creds_enc = encrypt_text(google_json, settings)
    db.add(user)
    db.commit()


def _apply_etl_collect_report(
    db: Session,
    user: User,
    settings: Settings,
    report: CollectResult,
    google_json: str,
    google_changed: bool,
    ics_created_total: int,
    ics_err: str | None,
    *,
    course_list_scanned: bool,
    ical_feed_configured: bool = False,
    ical_sync_attempted: bool = False,
    ical_sync_ok: bool | None = None,
) -> SyncResult:
    """eTL 수집 결과를 DB·Google에 반영(기존 run_user_sync eTL 분기와 동일)."""
    if report.collect_failed_note:
        if report.new_items:
            service, fresh_google_json = ensure_calendar_service(google_json)
            created = sync_assignments_to_calendar(service, report.new_items)
            if fresh_google_json != google_json:
                user.google_creds_enc = encrypt_text(fresh_google_json, settings)
            db.add(user)
            db.commit()
            msg = str(report.collect_failed_note)
            if created:
                msg += f" 그때까지 찾은 일정 {created}건은 캘린더에 반영했습니다."
            if ics_created_total:
                msg = f"(캘린더 구독 {ics_created_total}건은 먼저 반영됨) " + msg
            if ics_err:
                msg = f"(캘린더 구독 오류: {ics_err}) " + msg
            return _diag(
                report,
                new_count=len(report.new_items),
                created=created,
                ics_created=ics_created_total,
                message=msg,
                course_list_scanned=course_list_scanned,
                ical_feed_configured=ical_feed_configured,
                ical_sync_attempted=ical_sync_attempted,
                ical_sync_ok=ical_sync_ok,
            )
        _commit_user_google_maybe(db, user, settings, google_json, google_changed)
        msg = str(report.collect_failed_note)
        if ics_created_total:
            msg = f"(iCal {ics_created_total}건은 이미 반영했습니다.) " + msg
        if ics_err:
            msg = f"(iCal 오류: {ics_err}) " + msg
        return _diag(
            report,
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=msg,
            course_list_scanned=course_list_scanned,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )

    if not report.login_ok:
        _commit_user_google_maybe(db, user, settings, google_json, google_changed)
        msg = "eTL 로그인에 실패했습니다."
        if report.login_note:
            msg += " " + str(report.login_note)
        if ics_created_total:
            msg = f"(iCal {ics_created_total}건은 이미 반영했습니다.) " + msg
        if ics_err:
            msg = f"(iCal 오류: {ics_err}) " + msg
        return _diag(
            report,
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=msg,
            course_list_scanned=course_list_scanned,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )

    if report.courses_found == 0 and course_list_scanned:
        _commit_user_google_maybe(db, user, settings, google_json, google_changed)
        msg = "⚠️ 강의를 찾지 못했어요. 먼저 eTL 로그인을 해주세요."
        if ics_err:
            msg = f"(iCal 오류: {ics_err}) " + msg
        return _diag(
            report,
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=msg,
            course_list_scanned=course_list_scanned,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )

    if (
        report.assign_links_found == 0
        and report.quiz_links_found == 0
        and report.announcement_keyword_hits == 0
    ):
        _commit_user_google_maybe(db, user, settings, google_json, google_changed)
        msg = "과제·퀴즈 링크와 중간/기말 공지 키워드를 찾지 못했습니다."
        if ics_err:
            msg = f"(iCal 오류: {ics_err}) " + msg
        return _diag(
            report,
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=msg,
            course_list_scanned=course_list_scanned,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )

    if not report.new_items:
        _commit_user_google_maybe(db, user, settings, google_json, google_changed)
        msg = "새 항목이 없습니다. (Google 캘린더에 동일 etl_id 일정이 있으면 다시 넣지 않습니다)"
        if ics_err:
            msg = f"(iCal 오류: {ics_err}) " + msg
        return _diag(
            report,
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=msg,
            course_list_scanned=course_list_scanned,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )

    service, fresh_google_json = ensure_calendar_service(google_json)
    created = sync_assignments_to_calendar(service, report.new_items)

    if fresh_google_json != google_json:
        user.google_creds_enc = encrypt_text(fresh_google_json, settings)

    db.add(user)
    db.commit()

    extra = ""
    if ics_err:
        extra = f" (iCal 경고: {ics_err})"
    elif ics_created_total:
        extra = f" (iCal에서 {ics_created_total}건 먼저 반영)"

    return _diag(
        report,
        new_count=len(report.new_items),
        created=created,
        ics_created=ics_created_total,
        message=extra.strip() if extra else None,
        course_list_scanned=course_list_scanned,
        ical_feed_configured=ical_feed_configured,
        ical_sync_attempted=ical_sync_attempted,
        ical_sync_ok=ical_sync_ok,
    )


def run_user_sync(
    db: Session,
    user: User,
    settings: Settings,
) -> SyncResult:
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
        )

    (
        google_json,
        google_changed,
        ics_created_total,
        ics_err,
        ical_feed_configured,
        ical_sync_ok,
    ) = _ical_merge_only(user, settings, google_json)
    ical_sync_attempted = ical_feed_configured

    has_canvas = bool(user.canvas_token_enc)

    _commit_user_google_maybe(db, user, settings, google_json, google_changed)
    ok_report = CollectResult(login_ok=True)
    parts: list[str] = []
    if ics_err:
        parts.append("[캘린더 구독] " + ics_err)
    if ics_created_total:
        parts.append(f"캘린더 구독에서 {ics_created_total}건 Google에 반영했습니다.")
    if not ical_feed_configured:
        parts.append(
            "myetl 캘린더 → «URL 주소 가져오기»로 받은 구독 링크를 저장하면, "
            "이 버튼만으로 동기화할 수 있어요."
        )
    if settings.deploy_env == "production":
        parts.append(
            "클라우드에서는 Canvas API 토큰 +「전체 동기화」(서버) 또는 구독 URL·Chrome 확장을 이용해 주세요."
        )
    elif has_canvas:
        parts.append(
            "과제·퀴즈 반영은 Canvas API 토큰 저장 후「전체 동기화」 또는 Chrome 확장 프로그램을 사용하세요."
        )
    else:
        parts.append(
            "과제·퀴즈 반영은 myetl에서 Canvas API 토큰을 발급해 저장하거나, Chrome 확장 프로그램을 사용하세요."
        )
    return _diag(
        ok_report,
        new_count=0,
        created=0,
        ics_created=ics_created_total,
        message=" ".join(parts) if parts else None,
        course_list_scanned=False,
        ical_feed_configured=ical_feed_configured,
        ical_sync_attempted=ical_sync_attempted,
        ical_sync_ok=ical_sync_ok,
        ical_ui_context=True,
    )


def run_etl_prepare_browser(
    _db: Session,
    user: User,
    settings: Settings,
) -> SyncResult:
    """레거시 엔드포인트 호환: 서버에 eTL 비밀번호를 저장하지 않으므로 Selenium 준비는 하지 않습니다."""
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
        )

    return SyncResult(
        new_assignments=0,
        calendar_events_created=0,
        ics_events_created=0,
        message=(
            "서버에 eTL(myetl) 로그인 비밀번호를 저장하는 기능은 종료되었습니다. "
            "Canvas API 토큰(연결 설정)과「전체 동기화」, 캘린더 구독 URL·Chrome 확장 프로그램을 이용해 주세요."
        ),
        login_ok=False,
        courses_found=0,
        assign_links_found=0,
        quiz_links_found=0,
        announcement_keyword_hits=0,
        login_note=None,
    )


def run_etl_continue_sync(
    db: Session,
    user: User,
    settings: Settings,
) -> SyncResult:
    """prepare로 연 브라우저에서 세션을 확인한 뒤 수집·Google 반영."""
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
        )

    if settings.deploy_env == "production":
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="클라우드 배포 환경에서는 브라우저 eTL 동기화를 사용할 수 없습니다. "
            "「📅 캘린더 간편 동기화」를 사용해 주세요.",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
        )

    driver = etl_driver_peek(user.id)
    if driver is None:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="열어 둔 브라우저 동기화 세션이 없습니다. "
            "과제·퀴즈는 Canvas API 토큰과「전체 동기화」 또는 Chrome 확장을 사용해 주세요. "
            "(구독 URL만 반영하려면「간편 동기화」를 사용하세요.)",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
        )

    (
        google_json,
        google_changed,
        ics_created_total,
        ics_err,
        ical_feed_configured,
        ical_sync_ok,
    ) = _ical_merge_only(user, settings, google_json)
    ical_sync_attempted = ical_feed_configured
    _commit_user_google_maybe(db, user, settings, google_json, google_changed)

    headed_pause = 0.0 if settings.etl_headless else float(settings.etl_headed_pause_sec)
    report: CollectResult | None = None
    sc = _etl_scraper()
    try:
        set_progress(
            user.id,
            running=True,
            phase="starting",
            course_index=0,
            course_total=0,
            course_name="",
        )
        ok, note = sc.login_resume_session(driver, allow_interactive_mfa=True)
        if not ok:
            report = CollectResult(login_ok=False, login_note=note)
            return _apply_etl_collect_report(
                db,
                user,
                settings,
                report,
                google_json,
                google_changed,
                ics_created_total,
                ics_err,
                course_list_scanned=False,
                ical_feed_configured=ical_feed_configured,
                ical_sync_attempted=ical_sync_attempted,
                ical_sync_ok=ical_sync_ok,
            )

        session_seen: set[str] = set()
        try:
            report = sc.collect_etl_activities_with_existing_driver(
                driver,
                session_seen,
                sync_deadline=time.monotonic() + sc.SYNC_MAX_SEC,
                progress_cb=lambda d: set_progress(user.id, running=True, **d),
            )
        except Exception as e:
            report = CollectResult(
                login_ok=True,
                updated_seen=set(session_seen),
                collect_failed_note=f"수집 중 오류가 발생했습니다: {e}",
            )
        return _apply_etl_collect_report(
            db,
            user,
            settings,
            report,
            google_json,
            google_changed,
            ics_created_total,
            ics_err,
            course_list_scanned=True,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )
    finally:
        clear_progress(user.id)
        try:
            if headed_pause > 0:
                time.sleep(headed_pause)
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass
        etl_driver_remove(user.id, quit_driver=False)
