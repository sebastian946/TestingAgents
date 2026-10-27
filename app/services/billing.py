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
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
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


class BillingError(Exception):
    """A subscription action we cannot perform: no subscription, no API key, provider error."""


def configured() -> bool:
    return bool(settings.lemonsqueezy_webhook_secret)


def api_configured() -> bool:
    return bool(settings.lemonsqueezy_api_key)


def checkout_api_configured() -> bool:
    return api_configured() and bool(settings.lemonsqueezy_store_id)


def variant_for(plan_code: str) -> str | None:
    return {"starter": settings.lemonsqueezy_variant_starter, "pro": settings.lemonsqueezy_variant_pro}.get(plan_code) or None


def share_link_for(plan_code: str) -> str | None:
    return {"starter": settings.lemonsqueezy_checkout_starter_url, "pro": settings.lemonsqueezy_checkout_pro_url}.get(plan_code) or None


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
    can_cancel: bool = False
    can_resume: bool = False

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
        can_cancel=bool(subscription and subscription.status in ENTITLED_STATUSES and api_configured()),
        can_resume=bool(
            subscription and subscription.status == "cancelled" and api_configured()
            and _aware(subscription.current_period_end) is not None and _aware(subscription.current_period_end) > now
        ),
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


# ------------------------------------------------- cancel / resume through the API

API_BASE = "https://api.lemonsqueezy.com/v1"
API_TIMEOUT = 15


def _api(method: str, path: str, json: dict | None = None) -> dict:
    if not api_configured():
        raise BillingError("Subscription changes are not enabled on this server.")
    headers = {
        "Authorization": f"Bearer {settings.lemonsqueezy_api_key}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    try:
        response = requests.request(method, f"{API_BASE}{path}", headers=headers, json=json, timeout=API_TIMEOUT)
    except requests.RequestException as exc:
        raise BillingError(f"The payment provider did not answer ({exc.__class__.__name__}).")
    if response.status_code >= 400:
        raise BillingError(f"The payment provider refused the change (HTTP {response.status_code}).")
    try:
        return response.json()
    except ValueError:
        raise BillingError("The payment provider answered something that is not JSON.")


def _store_from_api(db: Session, subscription: Subscription, body: dict) -> Subscription:
    """Apply the subscription object the API returned (same shape as the webhook's data).
    The webhook for the same change arrives a moment later and is idempotent."""
    attributes = (body.get("data") or {}).get("attributes") or {}
    status = str(attributes.get("status") or subscription.status)
    period_end = _parse_time(attributes.get("ends_at")) or _parse_time(attributes.get("renews_at"))
    return db_crud.upsert_subscription(
        db,
        user_id=subscription.user_id,
        status=status,
        current_period_end=period_end or subscription.current_period_end,
        customer_portal_url=(attributes.get("urls") or {}).get("customer_portal") or subscription.customer_portal_url,
    )


def cancel_subscription(db: Session, user: User) -> Subscription:
    """Cancel at the end of the paid period (the provider's DELETE does exactly that)."""
    subscription = db_crud.get_subscription(db, user.id)
    if subscription is None or subscription.status not in ENTITLED_STATUSES:
        raise BillingError("There is no active subscription to cancel.")
    body = _api("DELETE", f"/subscriptions/{subscription.provider_subscription_id}")
    return _store_from_api(db, subscription, body)


def resume_subscription(db: Session, user: User) -> Subscription:
    """Undo a cancellation before the period ends."""
    subscription = db_crud.get_subscription(db, user.id)
    if subscription is None or subscription.status != "cancelled":
        raise BillingError("There is no cancelled subscription to resume.")
    body = _api(
        "PATCH",
        f"/subscriptions/{subscription.provider_subscription_id}",
        json={"data": {"type": "subscriptions", "id": subscription.provider_subscription_id, "attributes": {"cancelled": False}}},
    )
    return _store_from_api(db, subscription, body)


# ----------------------------------------------------------- per-user checkouts (WTA-37)

CHECKOUT_TTL_HOURS = 24


def create_checkout(db: Session, user: User, plan_code: str) -> str:
    """A checkout URL for this user and plan. With the API key and the store id it is created
    through the Checkouts API (email prefilled, our user id as custom data, brand color,
    receipt button and redirect back to /billing); otherwise it is the product's share link
    with the email and user id appended. Raises BillingError when the plan is not sold."""
    variant = variant_for(plan_code)
    if plan_code not in PLANS or PLANS[plan_code].price_usd == 0:
        raise BillingError(f"The {plan_code!r} plan cannot be purchased.")
    if not checkout_api_configured() or not variant:
        link = checkout_url(share_link_for(plan_code), user)
        if not link:
            raise BillingError("Payments are not enabled on this server.")
        return link
    subscription = db_crud.get_subscription(db, user.id)
    app_url = settings.app_url.rstrip("/")
    expires = (_now() + timedelta(hours=CHECKOUT_TTL_HOURS)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    body = {"data": {
        "type": "checkouts",
        "attributes": {
            "checkout_data": {"email": user.email, "custom": {"user_id": str(user.id)}},
            "checkout_options": {"embed": False, "logo": True, "desc": True, "discount": True, "media": False,
                                 "button_color": "#b8431a"},
            "product_options": {
                "enabled_variants": [int(variant)],
                "redirect_url": f"{app_url}/billing?checkout=success",
                "receipt_button_text": "Back to Tests Wise",
                "receipt_link_url": f"{app_url}/billing",
                "receipt_thank_you_note": "Your plan is active now. Create your next job from the dashboard.",
            },
            "expires_at": expires,
            "preview": False,
        },
        "relationships": {
            "store": {"data": {"type": "stores", "id": str(settings.lemonsqueezy_store_id)}},
            "variant": {"data": {"type": "variants", "id": str(variant)}},
        },
    }}
    if subscription and subscription.provider_customer_id:
        body["data"]["attributes"]["checkout_data"]["custom"]["customer_id"] = str(subscription.provider_customer_id)
    response = _api("POST", "/checkouts", json=body)
    url = ((response.get("data") or {}).get("attributes") or {}).get("url")
    if not url:
        raise BillingError("The payment provider did not return a checkout URL.")
    return url
