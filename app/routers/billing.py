from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_current_user
from app.models import User
from app.schemas import BillingStatus
from app.config import Settings, get_settings

router = APIRouter()


@router.get("/status", response_model=BillingStatus)
def billing_status(user: User = Depends(get_current_user)) -> BillingStatus:
    return BillingStatus(
        plan=user.plan,
        stripe_customer_id=user.stripe_customer_id,
        billing_portal_available=False,
    )


@router.post("/checkout")
def create_checkout_session(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """구독 결제 — Stripe 연동 시 구현."""
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=501, detail="결제(Stripe) 연동 전입니다.")
    raise HTTPException(status_code=501, detail="Checkout 세션 생성은 아직 구현되지 않았습니다.")
