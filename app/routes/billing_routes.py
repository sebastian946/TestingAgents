"""Billing (WTA-30 / WTA-34, ADR-09): the caller's plan, Stripe Checkout, the Customer
Portal and the Stripe webhook."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from config.plans import PLANS
from db.conn import get_db
from db.models_db.models_db import User
from models.models import BillingInfo, CheckoutRequest, PlanRead, RedirectUrl
from security.auth import get_current_user, require_verified_email
from services import billing

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/me", response_model=BillingInfo, summary="The caller's plan, this month's usage and whether payments are on")
def my_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    usage = billing.usage_for(db, user)
    return BillingInfo(
        plan=PlanRead.model_validate(usage.plan),
        used_this_month=usage.used_this_month,
        remaining=usage.remaining,
        runs_this_month=usage.runs_this_month,
        runs_remaining=usage.runs_remaining,
        status=usage.status,
        current_period_end=usage.current_period_end,
        can_manage=usage.can_manage,
        payments_enabled=billing.configured(),
        plans=[PlanRead.model_validate(p) for p in PLANS.values()],
    )


@router.post("/checkout", response_model=RedirectUrl, summary="Start a Stripe Checkout for a paid plan")
def checkout(payload: CheckoutRequest, user: User = Depends(require_verified_email), db: Session = Depends(get_db)):
    """Returns the hosted Checkout URL; the dashboard redirects there. Stripe sends the user
    back to /billing?checkout=success|cancelled, and the webhook records the purchase."""
    if payload.plan not in PLANS or PLANS[payload.plan].price_usd == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Choose a paid plan.")
    try:
        return RedirectUrl(url=billing.create_checkout(db, user, payload.plan))
    except billing.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.post("/portal", response_model=RedirectUrl, summary="Open the Stripe Customer Portal (invoices, card, cancel)")
def portal(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return RedirectUrl(url=billing.create_portal(db, user))
    except billing.BillingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/webhook/stripe",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,  # called by Stripe, not by users
)
async def stripe_webhook(
    request: Request, db: Session = Depends(get_db), stripe_signature: str | None = Header(default=None),
):
    """Stripe's events. The signature is checked on the raw body before anything is parsed;
    an invalid one is a 400 and nothing is stored. A 2xx tells Stripe not to retry, so
    ignored events answer 200 too."""
    if not billing.configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not configured.")
    raw = await request.body()
    try:
        event = billing.construct_event(raw, stripe_signature)
        outcome = billing.apply_event(db, event)
    except billing.WebhookError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    print(f"[billing] {outcome}", flush=True)
    return {"detail": outcome}
