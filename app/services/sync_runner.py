import json
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
from calendar_service import add_assignment_to_calendar, ensure_calendar_service, sync_assignments_to_calendar


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


def _seen_set(user: User) -> set[str]:
    try:
        data = json.loads(user.seen_assignment_ids or "[]")
        if not isinstance(data, list):
            return set()
        return {str(x) for x in data}
    except json.JSONDecodeError:
        return set()


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
    merged_seen: set[str],
    google_json: str,
) -> tuple[set[str], str, bool, int, str | None, bool, bool | None]:
    """iCal 구독만 반영. DB는 건드리지 않고 merged_seen·google_json을 갱신.

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
        return merged_seen, google_json, google_changed, ics_created_total, ics_err, False, None
    ical_ok: bool | None = True
    try:
        ics_body = fetch_moodle_calendar_ics(feed_plain)
        items = ical_to_assignment_items(ics_body)
        if not items and "BEGIN:VEVENT" in (ics_body or "").upper():
            ics_err = (
                "구독 ICS에 이벤트는 있으나, 반복 일정(RRULE)이거나 시작일시(DTSTART)가 없어 "
                "이 앱에서 Google로 넣을 항목이 없습니다."
            )
        fresh = [it for it in items if it["id"] not in merged_seen]
        if fresh:
            service, gj = ensure_calendar_service(google_json)
            for it in fresh:
                if add_assignment_to_calendar(service, it):
                    ics_created_total += 1
                    merged_seen.add(it["id"])
            if ics_created_total == 0:
                hint = (
                    "Google Calendar에 새 일정을 추가하지 못했습니다. "
                    "OAuth 연결(캘린더 쓰기 권한)·API 오류·중복일 수 있습니다."
                )
                ics_err = f"{ics_err} {hint}" if ics_err else hint
            if gj != google_json:
                google_json = gj
                google_changed = True
    except Exception as e:
        ics_err = str(e)
        ical_ok = False
    return merged_seen, google_json, google_changed, ics_created_total, ics_err, True, ical_ok


def _commit_seen_and_google_with_settings(
    db: Session,
    user: User,
    settings: Settings,
    merged_seen: set[str],
    google_json: str,
    google_changed: bool,
) -> None:
    user.seen_assignment_ids = json.dumps(sorted(merged_seen), ensure_ascii=False)
    if google_changed:
        user.google_creds_enc = encrypt_text(google_json, settings)
    db.add(user)
    db.commit()


def _apply_etl_collect_report(
    db: Session,
    user: User,
    settings: Settings,
    report: CollectResult,
    merged_seen: set[str],
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
            user.seen_assignment_ids = json.dumps(sorted(report.updated_seen), ensure_ascii=False)
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
        _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)
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
        _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)
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
        _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)
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
        _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)
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
        user.seen_assignment_ids = json.dumps(sorted(report.updated_seen), ensure_ascii=False)
        if google_changed:
            user.google_creds_enc = encrypt_text(google_json, settings)
        db.add(user)
        db.commit()
        msg = "새 항목이 없습니다. (이미 동기화된 과제·퀴즈·공지 언급 URL/해시는 제외됩니다)"
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

    user.seen_assignment_ids = json.dumps(sorted(report.updated_seen), ensure_ascii=False)
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

    merged_seen = _seen_set(user)
    (
        merged_seen,
        google_json,
        google_changed,
        ics_created_total,
        ics_err,
        ical_feed_configured,
        ical_sync_ok,
    ) = _ical_merge_only(user, settings, merged_seen, google_json)
    ical_sync_attempted = ical_feed_configured

    etl_user = (decrypt_text(user.etl_username_enc, settings) or "").strip()
    etl_pass = (decrypt_text(user.etl_password_enc, settings) or "").strip("\r\n")

    _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)
    ok_report = CollectResult(login_ok=True)
    parts: list[str] = []
    if ics_err:
        parts.append("[캘린더 구독] " + ics_err)
    if ics_created_total:
        parts.append(f"캘린더 구독에서 {ics_created_total}건 Google에 반영했습니다.")
    if not ical_feed_configured:
        parts.append(
            "myetl 캘린더 → «URL 주소 가져오기»로 받은 구독 링크를 저장하면, "
            "eTL 로그인 없이 이 버튼만으로 동기화할 수 있어요."
        )
    if settings.deploy_env == "production":
        if etl_user and etl_pass:
            parts.append(
                "클라우드 배포에서는 브라우저 eTL 동기화를 사용할 수 없습니다. "
                "구독 URL·「📅 캘린더 간편 동기화」만 이용해 주세요."
            )
    elif etl_user and etl_pass:
        parts.append(
            "과제·퀴즈·공지까지 하려면 「🔐 eTL 로그인」→ myetl 로그인·MFA → 「🔄 전체 동기화 시작」 순서를 사용하세요."
        )
    elif (not etl_user or not etl_pass) and settings.deploy_env != "production":
        parts.append(
            "과제·퀴즈·공지까지 하려면 eTL 아이디·비밀번호를 저장한 뒤, "
            "「🔐 eTL 로그인」→「🔄 전체 동기화 시작」 순서(로컬)를 사용하세요."
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
    db: Session,
    user: User,
    settings: Settings,
) -> SyncResult:
    """eTL 통합로그인까지 자동으로 진행한 뒤 브라우저를 열어 둡니다. 이후 `run_etl_continue_sync` 호출."""
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

    etl_user = (decrypt_text(user.etl_username_enc, settings) or "").strip()
    etl_pass = (decrypt_text(user.etl_password_enc, settings) or "").strip("\r\n")
    if not etl_user or not etl_pass:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="eTL 아이디·비밀번호를 먼저 저장해 주세요.",
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
            message="클라우드 배포 환경에서는 브라우저 eTL 동기화(Selenium)를 사용할 수 없습니다. "
            "「📅 캘린더 간편 동기화」로 myetl 캘린더 구독 URL만 반영해 주세요.",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
        )

    if settings.etl_headless:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="브라우저 로그인을 띄우려면 서버 설정에서 ETL_HEADLESS=false 가 필요합니다.",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
        )

    merged_seen = _seen_set(user)
    (
        merged_seen,
        google_json,
        google_changed,
        ics_created_total,
        ics_err,
        ical_feed_configured,
        ical_sync_ok,
    ) = _ical_merge_only(user, settings, merged_seen, google_json)
    ical_sync_attempted = ical_feed_configured
    _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)

    etl_driver_remove(user.id, quit_driver=True)
    parts_head: list[str] = []
    if ics_err:
        parts_head.append("iCal: " + ics_err)
    if ics_created_total:
        parts_head.append(f"iCal {ics_created_total}건 반영.")

    sc = _etl_scraper()
    driver = None
    try:
        driver = sc.get_driver(
            headless=False,
            browser=settings.etl_browser,
            chrome_debugger_address=settings.etl_chrome_debugger_address,
        )
        ok, note = sc.login(
            driver,
            etl_user,
            etl_pass,
            allow_interactive_mfa=True,
            wait_for_session_after_submit=False,
        )

        if not ok:
            # 자동 로그인이 중간에서 실패해도 창을 닫지 않음 → 사용자가 수동으로 이어갈 수 있음.
            etl_driver_store(user.id, driver)
            driver = None
            tail = [
                "자동 로그인은 여기까지 진행하지 못했지만 브라우저 창은 닫지 않았습니다.",
                "창에서 직접 통합로그인·MFA를 완료한 뒤 이 페이지에서 「🔄 전체 동기화 시작」을 눌러 주세요.",
            ]
            if note:
                tail.append("(자동 시도 안내: " + str(note) + ")")
            msg = " ".join(parts_head + tail) if parts_head else " ".join(tail)
            return _diag(
                CollectResult(login_ok=False, login_note=note),
                new_count=0,
                created=0,
                ics_created=ics_created_total,
                message=msg,
                etl_awaiting_user=True,
                ical_feed_configured=ical_feed_configured,
                ical_sync_attempted=ical_sync_attempted,
                ical_sync_ok=ical_sync_ok,
            )

        etl_driver_store(user.id, driver)
        driver = None
        dbg_hint: list[str] = []
        if str(settings.etl_browser).lower() in ("chrome", "system"):
            dbg_hint.append(
                "Chrome은 미리 `--remote-debugging-port=9222` 로 실행해 두면(로그인·MFA 완료 후) "
                "「🔐 eTL 로그인」이 그 인스턴스에 붙습니다."
            )
        parts = parts_head + dbg_hint + [
            "브라우저에서 로그인·MFA를 마친 뒤 이 페이지에서 「🔄 전체 동기화 시작」을 눌러 주세요.",
        ]
        return _diag(
            CollectResult(login_ok=True),
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=" ".join(parts),
            etl_awaiting_user=True,
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
        )
    except Exception as e:
        if driver is not None:
            try:
                etl_driver_store(user.id, driver)
                driver = None
                tail = [
                    f"브라우저 준비 중 오류가 있었지만 창은 닫지 않았습니다: {e}",
                    "창을 확인한 뒤 직접 로그인하거나, 문제가 있으면 창을 닫고 「🔐 eTL 로그인」을 다시 눌러 주세요.",
                ]
                msg = " ".join(parts_head + tail) if parts_head else " ".join(tail)
                return _diag(
                    CollectResult(login_ok=False, login_note=str(e)),
                    new_count=0,
                    created=0,
                    ics_created=ics_created_total,
                    message=msg,
                    etl_awaiting_user=True,
                    ical_feed_configured=ical_feed_configured,
                    ical_sync_attempted=ical_sync_attempted,
                    ical_sync_ok=ical_sync_ok,
                )
            except Exception:
                pass
        try:
            if driver is not None:
                driver.quit()
        except Exception:
            pass
        return _diag(
            CollectResult(login_ok=False, login_note=str(e)),
            new_count=0,
            created=0,
            ics_created=ics_created_total,
            message=f"브라우저를 유지할 수 없습니다: {e}",
            ical_feed_configured=ical_feed_configured,
            ical_sync_attempted=ical_sync_attempted,
            ical_sync_ok=ical_sync_ok,
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

    etl_user = (decrypt_text(user.etl_username_enc, settings) or "").strip()
    etl_pass = (decrypt_text(user.etl_password_enc, settings) or "").strip("\r\n")
    if not etl_user or not etl_pass:
        return SyncResult(
            new_assignments=0,
            calendar_events_created=0,
            ics_events_created=0,
            message="eTL 계정이 저장되어 있지 않습니다.",
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
            message="먼저 「🔐 eTL 로그인」을 눌러 주세요. "
            "(구독 URL만 반영하려면 「📅 캘린더 간편 동기화」를 사용하세요.)",
            login_ok=False,
            courses_found=0,
            assign_links_found=0,
            quiz_links_found=0,
            announcement_keyword_hits=0,
            login_note=None,
        )

    merged_seen = _seen_set(user)
    (
        merged_seen,
        google_json,
        google_changed,
        ics_created_total,
        ics_err,
        ical_feed_configured,
        ical_sync_ok,
    ) = _ical_merge_only(user, settings, merged_seen, google_json)
    ical_sync_attempted = ical_feed_configured
    _commit_seen_and_google_with_settings(db, user, settings, merged_seen, google_json, google_changed)

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
                merged_seen,
                google_json,
                google_changed,
                ics_created_total,
                ics_err,
                course_list_scanned=False,
                ical_feed_configured=ical_feed_configured,
                ical_sync_attempted=ical_sync_attempted,
                ical_sync_ok=ical_sync_ok,
            )

        try:
            report = sc.collect_etl_activities_with_existing_driver(
                driver,
                merged_seen,
                sync_deadline=time.monotonic() + sc.SYNC_MAX_SEC,
                progress_cb=lambda d: set_progress(user.id, running=True, **d),
            )
        except Exception as e:
            report = CollectResult(
                login_ok=True,
                updated_seen=set(merged_seen),
                collect_failed_note=f"수집 중 오류가 발생했습니다: {e}",
            )
        return _apply_etl_collect_report(
            db,
            user,
            settings,
            report,
            merged_seen,
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
