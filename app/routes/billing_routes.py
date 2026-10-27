"""Billing (WTA-30, ADR-09): what plan the caller is on, and the provider's webhook."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from config.plans import PLANS
from config.variables import settings
from db.conn import get_db
from db.models_db.models_db import User
from models.models import BillingInfo, CheckoutRequest, PlanRead, RedirectUrl
from security.auth import get_current_user
from services import billing

router = APIRouter(prefix="/billing", tags=["billing"])


def _info(db: Session, user: User) -> BillingInfo:
    usage = billing.usage_for(db, user)
    return BillingInfo(
        plan=PlanRead.model_validate(usage.plan),
        used_this_month=usage.used_this_month,
        remaining=usage.remaining,
        runs_this_month=usage.runs_this_month,
        runs_remaining=usage.runs_remaining,
        status=usage.status,
        current_period_end=usage.current_period_end,
        customer_portal_url=usage.customer_portal_url,
        can_cancel=usage.can_cancel,
        can_resume=usage.can_resume,
        plans=[PlanRead.model_validate(p) for p in PLANS.values()],
        checkout_urls={
            code: url for code, url in {
                "starter": billing.checkout_url(settings.lemonsqueezy_checkout_starter_url, user),
                "pro": billing.checkout_url(settings.lemonsqueezy_checkout_pro_url, user),
            }.items() if url
        },
    )


@router.get("/me", response_model=BillingInfo, summary="The caller's plan, this month's usage and the upgrade links")
def my_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _info(db, user)


@router.post("/checkout", response_model=RedirectUrl, summary="A checkout URL for the caller and a paid plan")
def start_checkout(payload: CheckoutRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """WTA-37: the dashboard redirects to the returned URL. Created per user through the
    provider's API when it is configured, else the share link with the user's data appended."""
    try:
        return RedirectUrl(url=billing.create_checkout(db, user, payload.plan))
    except billing.BillingError as exc:
        code = status.HTTP_400_BAD_REQUEST if "cannot be purchased" in str(exc) else status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=str(exc))


@router.post("/cancel", response_model=BillingInfo, summary="Cancel the subscription at the end of the paid period")
def cancel_plan(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        billing.cancel_subscription(db, user)
    except billing.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return _info(db, user)


@router.post("/resume", response_model=BillingInfo, summary="Undo a cancellation before the period ends")
def resume_plan(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        billing.resume_subscription(db, user)
    except billing.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return _info(db, user)


@router.post(
    "/webhook/lemonsqueezy",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,  # called by the provider, not by users
)
async def lemonsqueezy_webhook(
    request: Request, db: Session = Depends(get_db), x_signature: str | None = Header(default=None),
):
    """The provider's events. The signature is checked on the raw body before anything is
    parsed; an invalid one is a 400 and nothing is stored. A 2xx tells the provider not to
    retry, so ignored events answer 200 too."""
    if not billing.configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not configured.")
    raw = await request.body()
    if not billing.verify_signature(raw, x_signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature.")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON.")
    try:
        outcome = billing.apply_webhook(db, payload)
    except billing.WebhookError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    print(f"[billing] {outcome}", flush=True)
    return {"detail": outcome}
