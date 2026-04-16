import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import SyncLog, User
from app.schemas import ClientSyncImport, SyncLogOut, SyncProgressOut, SyncResult
from app.services.auto_sync import run_auto_sync_all
from app.services.client_sync import import_from_client
from app.services.sync_progress import get_progress
from app.services.canvas_sync import run_canvas_server_sync
from app.services.sync_runner import run_etl_continue_sync, run_etl_prepare_browser, run_user_sync

router = APIRouter()


@router.post("/auto-trigger")
def auto_sync_trigger(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    settings: Settings = Depends(get_settings),
):
    """cron-job.org에서 호출 — X-Cron-Secret 헤더로 인증 후 전체 사용자 auto_sync 실행."""
    expected = settings.cron_secret
    if not expected or not x_cron_secret:
        raise HTTPException(status_code=401, detail="Missing secret")
    if not hmac.compare_digest(x_cron_secret, expected):
        raise HTTPException(status_code=403, detail="Invalid secret")
    run_auto_sync_all()
    return JSONResponse({"status": "triggered"})


@router.get("/progress", response_model=SyncProgressOut)
def sync_progress(user: User = Depends(get_current_user)) -> SyncProgressOut:
    """`POST /api/sync/etl/continue` 진행 중 폴링(강의 N/M 스캔 등)."""
    d = get_progress(user.id)
    return SyncProgressOut(
        running=bool(d.get("running")),
        phase=str(d.get("phase") or ""),
        course_index=int(d.get("course_index") or 0),
        course_total=int(d.get("course_total") or 0),
        course_name=str(d.get("course_name") or ""),
    )


@router.post("/", response_model=SyncResult)
def run_sync(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SyncResult:
    """eTL에서 새 과제·퀴즈를 가져와 Google Calendar에 추가합니다. 응답에 스캔 요약이 포함됩니다."""
    print("=== 동기화 API 호출됨 === POST /api/sync/", flush=True)
    return run_user_sync(db, user, settings)


@router.post("/etl/prepare", response_model=SyncResult)
def etl_prepare_browser(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SyncResult:
    """eTL 통합로그인 브라우저를 엽니다. 로그인·MFA 후 `/api/sync/etl/continue`를 호출하세요."""
    print("=== 동기화 API 호출됨 === POST /api/sync/etl/prepare", flush=True)
    return run_etl_prepare_browser(db, user, settings)


@router.post("/etl/continue", response_model=SyncResult)
def etl_continue_sync(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SyncResult:
    """prepare 이후 세션을 확인하고 과제·퀴즈 수집 및 Google 반영을 진행합니다."""
    print("=== 동기화 API 호출됨 === POST /api/sync/etl/continue", flush=True)
    return run_etl_continue_sync(db, user, settings)


@router.post("/canvas", response_model=SyncResult)
def sync_canvas_server(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SyncResult:
    """저장된 Canvas API 토큰으로 myetl REST에서 과제·퀴즈를 가져와 Google Calendar에 반영합니다."""
    print("=== 동기화 API 호출됨 === POST /api/sync/canvas", flush=True)
    return run_canvas_server_sync(db, user, settings)


@router.get("/history", response_model=list[SyncLogOut])
def sync_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, le=200),
) -> list[SyncLog]:
    """최근 동기화에서 Google Calendar에 추가된 항목 목록."""
    rows = db.scalars(
        select(SyncLog)
        .where(SyncLog.user_id == user.id)
        .order_by(SyncLog.synced_at.desc())
        .limit(limit)
    ).all()
    return list(rows)


@router.post("/from-client", response_model=SyncResult)
def sync_from_client(
    body: ClientSyncImport,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SyncResult:
    """이미 myetl에 로그인된 브라우저·WebView에서 수집한 항목만 반영합니다. 서버 Selenium 없이 동작합니다."""
    print("=== 동기화 API 호출됨 === POST /api/sync/from-client", flush=True)
    return import_from_client(db, user, settings, body.items)
