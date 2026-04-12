from fastapi import APIRouter

from app.routers import auth, billing, google_oauth, me, sync

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, prefix="/me", tags=["me"])
api_router.include_router(google_oauth.router, prefix="/oauth/google", tags=["google"])
api_router.include_router(sync.router, prefix="/sync", tags=["sync"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
