"""
실행: 프로젝트 루트에서
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path


def _attach_etl_console_loggers() -> None:
    """eTL 동기화 진행 로그를 콘솔에 남깁니다(핸들러가 없을 때만)."""
    fmt = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    for name in ("etl_scraper", "app.services.sync_runner"):
        lg = logging.getLogger(name)
        if lg.handlers:
            continue
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.routers import api_router

BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    _attach_etl_console_loggers()
    _settings = get_settings()
    # 로컬 http에서 Google OAuth 테스트 시 필요
    if _settings.google_redirect_uri.startswith("http://"):
        import os

        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    init_db()
    print(f"디버거 주소: {_settings.etl_chrome_debugger_address}", flush=True)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/")
async def serve_dashboard():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/privacy")
async def privacy():
    return FileResponse(BASE_DIR / "static" / "privacy.html")


@app.get("/privacy-policy")
async def privacy_policy():
    return FileResponse(BASE_DIR / "static" / "privacy.html")


@app.get("/terms")
async def terms():
    return FileResponse(BASE_DIR / "static" / "terms.html")


@app.get("/google6613324f44353041.html")
async def google_site_verification():
    """Google Search Console 소유 확인용 파일."""
    return FileResponse(BASE_DIR / "static" / "google6613324f44353041.html")


@app.get("/sw.js")
async def service_worker():
    """PWA: 루트 scope로 등록하려면 Service-Worker-Allowed가 필요합니다."""
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript; charset=utf-8",
        headers={"Service-Worker-Allowed": "/"},
    )


static_dir = BASE_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
