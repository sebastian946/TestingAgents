"""Plans, monthly allowances and Stripe (WTA-30 / WTA-34, ADR-09).

We never touch cards. Stripe Checkout sells the subscription on a hosted page, Stripe
Billing renews it, the Customer Portal lets the user change card or cancel, and webhooks
tell us the outcome. Our side is small: which plan a user is on, how many jobs and runs
they used this month, and three thin endpoints (checkout, portal, webhook).

What we read from Stripe:
- `checkout.session.completed`: `client_reference_id` = our user id, `subscription`,
  `customer`; the plan comes from the subscription's price id.
- `customer.subscription.created|updated|deleted`: status, the price id of the first
  item, the period end (`current_period_end` on the subscription or, on newer API
  versions, on each item), `cancel_at_period_end`, `metadata.user_id`.
Price ids map to plans through `STRIPE_PRICE_STARTER` / `STRIPE_PRICE_PRO`.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import stripe
from sqlalchemy.orm import Session

from config.plans import FREE, PLANS, Plan
from config.variables import settings
from db import db_crud
from db.models_db.models_db import Subscription, User

PROVIDER = "stripe"
# Provider statuses under which the plan's allowance applies. "cancelled" keeps the plan
# until the paid period ends (checked separately); "expired", "unpaid", "paused" do not
ENTITLED_STATUSES = {"active", "on_trial", "past_due"}
# Stripe's vocabulary -> ours (kept from the first provider so the rest of the code and the
# dashboard do not care who bills)
STRIPE_STATUS = {
    "active": "active", "trialing": "on_trial", "past_due": "past_due", "canceled": "cancelled",
    "unpaid": "unpaid", "incomplete": "incomplete", "incomplete_expired": "expired", "paused": "paused",
}


class WebhookError(Exception):
    pass


class BillingError(Exception):
    pass


def configured() -> bool:
    return bool(settings.stripe_secret_key and settings.stripe_webhook_secret)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def plan_for(subscription: Subscription | None) -> Plan:
    """The plan whose allowance applies right now."""
    if subscription is None:
        return PLANS.get(settings.default_plan, FREE)
    plan = PLANS.get(subscription.plan, FREE)
    if subscription.status in ENTITLED_STATUSES:
        return plan
    period_end = _aware(subscription.current_period_end)
    if subscription.status == "cancelled" and period_end is not None and period_end > _now():
        return plan  # paid until the end of the period
    return PLANS.get(settings.default_plan, FREE)


@dataclass
class Usage:
    plan: Plan
    used_this_month: int
    status: str | None
    current_period_end: datetime | None
    can_manage: bool  # a Stripe customer exists: the portal can be opened
    runs_this_month: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.plan.jobs_per_month - self.used_this_month)

    @property
    def runs_remaining(self) -> int:
        return max(0, self.plan.test_runs_per_month - self.runs_this_month)


def usage_for(db: Session, user: User) -> Usage:
    subscription = db_crud.get_subscription(db, user.id)
    plan = plan_for(subscription)
    now = _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = db_crud.count_jobs_since(db, user.id, month_start)
    runs = db_crud.count_runs_since(db, user.id, month_start) if plan.test_runs_per_month else 0
    return Usage(
        plan=plan,
        used_this_month=used,
        runs_this_month=runs,
        status=subscription.status if subscription else None,
        current_period_end=_aware(subscription.current_period_end) if subscription else None,
        can_manage=bool(subscription and subscription.provider_customer_id and configured()),
    )


# ----------------------------------------------------------------- prices and plans

def price_for(plan_code: str) -> str | None:
    return {"starter": settings.stripe_price_starter, "pro": settings.stripe_price_pro}.get(plan_code) or None


def _plan_for_price(price_id: str | None) -> str | None:
    if not price_id:
        return None
    if price_id == settings.stripe_price_starter:
        return "starter"
    if price_id == settings.stripe_price_pro:
        return "pro"
    return None


def _client() -> Any:
    stripe.api_key = settings.stripe_secret_key
    return stripe


# ----------------------------------------------------------------- checkout and portal

def create_checkout(db: Session, user: User, plan_code: str) -> str:
    """A hosted Checkout page for `plan_code`; returns its URL. The user id travels as
    `client_reference_id` and in the subscription's metadata, so the webhook can tell us
    who bought even before we stored a customer id."""
    if not configured():
        raise BillingError("Billing is not configured on this server.")
    price = price_for(plan_code)
    if price is None:
        raise BillingError(f"No Stripe price is configured for the {plan_code} plan.")
    existing = db_crud.get_subscription(db, user.id)
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": f"{settings.app_url}/billing?checkout=success",
        "cancel_url": f"{settings.app_url}/billing?checkout=cancelled",
        "client_reference_id": str(user.id),
        "subscription_data": {"metadata": {"user_id": str(user.id), "plan": plan_code}},
        "allow_promotion_codes": True,
    }
    if settings.stripe_automatic_tax:
        params["automatic_tax"] = {"enabled": True}
        params["customer_update"] = {"address": "auto"}
    if existing and existing.provider_customer_id:
        params["customer"] = existing.provider_customer_id
    else:
        params["customer_email"] = user.email
    session = _client().checkout.Session.create(**params)
    return session.url


def create_portal(db: Session, user: User) -> str:
    """The Customer Portal (invoices, card, cancel). Needs a Stripe customer, which exists
    once the first checkout completed."""
    if not configured():
        raise BillingError("Billing is not configured on this server.")
    subscription = db_crud.get_subscription(db, user.id)
    if subscription is None or not subscription.provider_customer_id:
        raise BillingError("No subscription to manage yet.")
    session = _client().billing_portal.Session.create(
        customer=subscription.provider_customer_id, return_url=f"{settings.app_url}/billing",
    )
    return session.url


# ----------------------------------------------------------------- webhook

def construct_event(raw_body: bytes, signature: str | None) -> Any:
    """Verify the `Stripe-Signature` header and parse the event; WebhookError otherwise."""
    if not configured() or not signature:
        raise WebhookError("missing signature")
    try:
        return stripe.Webhook.construct_event(raw_body, signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:  # type: ignore[attr-defined]
        raise WebhookError(f"invalid signature: {exc.__class__.__name__}")


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _period_end(sub: Any) -> datetime | None:
    """`current_period_end` lives on the subscription (API versions before 2025-03) or on
    each item (after). Either way a unix timestamp."""
    value = _get(sub, "current_period_end")
    if not value:
        items = _get(_get(sub, "items", {}) or {}, "data", []) or []
        if items:
            value = _get(items[0], "current_period_end")
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None
    except (TypeError, ValueError):
        return None


def _price_id(sub: Any) -> str | None:
    items = _get(_get(sub, "items", {}) or {}, "data", []) or []
    if not items:
        return None
    price = _get(items[0], "price") or {}
    return _get(price, "id")


def _user_id_of(sub: Any, fallback: str | None = None) -> uuid.UUID:
    raw = _get(_get(sub, "metadata", {}) or {}, "user_id") or fallback
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise WebhookError("user_id missing from the subscription metadata")


def _store(db: Session, user_id: uuid.UUID, sub: Any, event: str) -> str:
    if db_crud.get_user(db, user_id) is None:
        raise WebhookError(f"unknown user {user_id}")
    plan = _plan_for_price(_price_id(sub))
    if plan is None:
        return f"ignored price {_price_id(sub)!r}"
    raw_status = str(_get(sub, "status") or "active")
    status = STRIPE_STATUS.get(raw_status, raw_status)
    if _get(sub, "cancel_at_period_end") and status == "active":
        status = "cancelled"  # keeps the plan until current_period_end (plan_for)
    customer = _get(sub, "customer")
    db_crud.upsert_subscription(
        db,
        user_id=user_id,
        plan=plan,
        status=status,
        provider=PROVIDER,
        provider_subscription_id=str(_get(sub, "id")),
        provider_customer_id=str(_get(customer, "id") if not isinstance(customer, str) else customer) or None,
        customer_portal_url=None,
        current_period_end=_period_end(sub),
    )
    return f"{event}: user {user_id} -> {plan} ({status})"


def apply_event(db: Session, event: Any) -> str:
    """Update the user's subscription from a verified event. Unknown events and prices are
    ignored (200): Stripe sends many kinds, and a product we did not configure must not
    break the handler."""
    kind = str(_get(event, "type") or "")
    obj = _get(_get(event, "data", {}) or {}, "object") or {}
    if kind == "checkout.session.completed":
        if _get(obj, "mode") != "subscription" or not _get(obj, "subscription"):
            return "ignored checkout (not a subscription)"
        sub_ref = _get(obj, "subscription")
        sub = _client().Subscription.retrieve(sub_ref if isinstance(sub_ref, str) else _get(sub_ref, "id"))
        return _store(db, _user_id_of(sub, _get(obj, "client_reference_id")), sub, kind)
    if kind.startswith("customer.subscription."):
        return _store(db, _user_id_of(obj), obj, kind)
    return f"ignored event {kind or '?'}"
