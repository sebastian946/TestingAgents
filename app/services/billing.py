"""Plans, monthly allowances and the Lemon Squeezy webhook (WTA-30, ADR-09).

We never touch cards. Lemon Squeezy (a Merchant of Record) sells the subscription, handles
taxes and refunds, and tells us the outcome through signed webhooks. Our side is small:
which plan a user is on, how many jobs they used this month, and the checkout links.

Webhook contract (https://docs.lemonsqueezy.com/help/webhooks):
- header `X-Signature`: HMAC-SHA256 of the raw body with the signing secret, hex;
- `meta.event_name`: subscription_created / updated / cancelled / resumed / expired /
  paused / unpaused / payment_success ...;
- `meta.custom_data.user_id`: our user id, passed through the checkout URL;
- `data.id`: the subscription id; `data.attributes`: status, variant_id, customer_id,
  renews_at, ends_at, urls.customer_portal.
"""
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy.orm import Session

from config.plans import FREE, PLANS, Plan
from config.variables import settings
from db import db_crud
from db.models_db.models_db import Subscription, User

PROVIDER = "lemonsqueezy"
# Provider statuses under which the plan's allowance applies. "cancelled" keeps the plan
# until the paid period ends (checked separately); "expired", "unpaid", "paused" do not
ENTITLED_STATUSES = {"active", "on_trial", "past_due"}


class WebhookError(Exception):
    pass


def configured() -> bool:
    return bool(settings.lemonsqueezy_webhook_secret)


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


def checkout_url(base: str | None, user: User) -> str | None:
    """The hosted checkout, with the email prefilled and our user id as custom data, so the
    webhook can tell us who bought."""
    if not base:
        return None
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}checkout[email]={quote(user.email)}&checkout[custom][user_id]={user.id}"


@dataclass
class Usage:
    plan: Plan
    used_this_month: int
    status: str | None
    current_period_end: datetime | None
    customer_portal_url: str | None

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
        customer_portal_url=subscription.customer_portal_url if subscription else None,
    )


# ----------------------------------------------------------------- webhook

def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    if not settings.lemonsqueezy_webhook_secret or not signature:
        return False
    expected = hmac.new(settings.lemonsqueezy_webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def _plan_for_variant(variant_id) -> str | None:
    variant = str(variant_id) if variant_id is not None else None
    if variant and variant == settings.lemonsqueezy_variant_starter:
        return "starter"
    if variant and variant == settings.lemonsqueezy_variant_pro:
        return "pro"
    return None


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def apply_webhook(db: Session, payload: dict) -> str:
    """Update the user's subscription from a verified event. Returns a short outcome for
    the log. Unknown events and variants are ignored (not errors): the provider sends more
    kinds than we use, and a product we did not configure must not break the handler."""
    meta = payload.get("meta") or {}
    event = str(meta.get("event_name") or "")
    if not event.startswith("subscription_"):
        return f"ignored event {event or '?'}"
    data = payload.get("data") or {}
    attributes = data.get("attributes") or {}
    user_id_raw = (meta.get("custom_data") or {}).get("user_id")
    try:
        user_id = uuid.UUID(str(user_id_raw))
    except (TypeError, ValueError):
        raise WebhookError("custom_data.user_id missing or not a UUID")
    if db_crud.get_user(db, user_id) is None:
        raise WebhookError(f"unknown user {user_id}")
    plan = _plan_for_variant(attributes.get("variant_id"))
    if plan is None:
        return f"ignored variant {attributes.get('variant_id')!r}"
    subscription_id = str(data.get("id") or "")
    if not subscription_id:
        raise WebhookError("data.id missing")

    status = str(attributes.get("status") or "active")
    period_end = _parse_time(attributes.get("ends_at")) or _parse_time(attributes.get("renews_at"))
    db_crud.upsert_subscription(
        db,
        user_id=user_id,
        plan=plan,
        status=status,
        provider=PROVIDER,
        provider_subscription_id=subscription_id,
        provider_customer_id=str(attributes.get("customer_id") or "") or None,
        customer_portal_url=(attributes.get("urls") or {}).get("customer_portal"),
        current_period_end=period_end,
    )
    return f"{event}: user {user_id} -> {plan} ({status})"
