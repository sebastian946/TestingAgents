"""WTA-30 / WTA-34: plans and monthly allowances, the Stripe webhook, checkout and portal,
account deletion and the retention sweep. In-process API on SQLite; `stripe` is stubbed,
no provider calls.
"""
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from config.variables import settings
from db import db_crud
from db.conn import Base, get_db
from db.models_db.models_db import EmailToken, Job, LlmCall, Page, Scenario, Subscription, User
from main import app
from routes import routes
from services import billing, cleanup
from tests.conftest import sign_in

URL = "https://shop.example/"
SECRET = "whsec-test"
STRIPE_STUB: dict = {}


class StubSignatureError(Exception):
    pass


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routes, "validate_public_url", lambda url: url)
    monkeypatch.setattr(routes, "add_new_job_to_queue", lambda job_id: None)
    monkeypatch.setattr(settings, "reports_dir", str(tmp_path / "reports"))
    monkeypatch.setattr(settings, "default_plan", "free")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "stripe_price_starter", "price_starter")
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro")
    monkeypatch.setattr(settings, "app_url", "https://app.example")
    STRIPE_STUB.clear()

    # The Stripe SDK, stubbed: signature check accepts SECRET, Checkout/Portal return URLs,
    # Subscription.retrieve returns what the test registered under STRIPE_STUB["subs"]
    def construct_event(raw, signature, secret):
        if signature != SECRET or secret != SECRET:
            raise StubSignatureError("bad signature")
        import json
        return json.loads(raw)

    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", staticmethod(construct_event))
    monkeypatch.setattr(billing.stripe, "error", SimpleNamespace(SignatureVerificationError=StubSignatureError), raising=False)
    monkeypatch.setattr(billing.stripe.checkout.Session, "create",
                        staticmethod(lambda **p: STRIPE_STUB.setdefault("checkouts", []).append(p) or SimpleNamespace(url="https://checkout.stripe.test/cs_1")))
    monkeypatch.setattr(billing.stripe.billing_portal.Session, "create",
                        staticmethod(lambda **p: STRIPE_STUB.setdefault("portals", []).append(p) or SimpleNamespace(url="https://portal.stripe.test/ps_1")))
    monkeypatch.setattr(billing.stripe.Subscription, "retrieve", staticmethod(lambda sid: STRIPE_STUB["subs"][sid]))

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.factory = factory
    client.user_id = sign_in(client)
    yield client
    app.dependency_overrides.clear()
    engine.dispose()


def subscription(user_id, price="price_pro", status="active", sub_id="sub_1", period_end=None, cancel_at_period_end=False):
    """A Stripe subscription object as the webhook carries it (dict form, like the SDK's)."""
    end = int((period_end or datetime(2026, 11, 19, tzinfo=timezone.utc)).timestamp())
    return {"id": sub_id, "object": "subscription", "status": status, "customer": "cus_9",
            "cancel_at_period_end": cancel_at_period_end, "metadata": {"user_id": str(user_id)},
            "items": {"data": [{"price": {"id": price}, "current_period_end": end}]}}


def webhook(client, event, obj, secret=SECRET):
    import json

    raw = json.dumps({"type": event, "data": {"object": obj}}).encode()
    return client.post("/billing/webhook/stripe", content=raw,
                       headers={"Stripe-Signature": secret, "Content-Type": "application/json"})


# ----------------------------------------------------------------- allowances

def test_free_plan_allows_three_jobs_a_month_then_402(client):
    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "free" and info["remaining"] == 3 and info["status"] is None
    assert info["payments_enabled"] is True and info["can_manage"] is False

    for _ in range(3):
        assert client.post("/jobs", json={"url": URL}).status_code == 202
    blocked = client.post("/jobs", json={"url": URL})
    assert blocked.status_code == 402
    detail = blocked.json()["detail"]
    assert detail == {"message": detail["message"], "plan": "free", "used": 3, "limit": 3}
    assert "Upgrade" in detail["message"]
    assert client.get("/billing/me").json()["remaining"] == 0


def test_jobs_from_last_month_do_not_count(client):
    for _ in range(3):
        client.post("/jobs", json={"url": URL})
    with client.factory() as db:
        for job in db_crud.list_jobs(db):
            job.created_at = datetime.now(timezone.utc).replace(day=1) - timedelta(days=2)
        db.commit()
    assert client.get("/billing/me").json()["used_this_month"] == 0
    assert client.post("/jobs", json={"url": URL}).status_code == 202


# ----------------------------------------------------------------- webhook

def test_checkout_creates_a_stripe_session_with_the_user_reference(client):
    r = client.post("/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200 and r.json()["url"] == "https://checkout.stripe.test/cs_1"
    (params,) = STRIPE_STUB["checkouts"]
    assert params["mode"] == "subscription" and params["line_items"] == [{"price": "price_pro", "quantity": 1}]
    assert params["client_reference_id"] == str(client.user_id)
    assert params["subscription_data"]["metadata"] == {"user_id": str(client.user_id), "plan": "pro"}
    assert params["customer_email"] == "tester@example.com" and "customer" not in params
    assert params["success_url"] == "https://app.example/billing?checkout=success"
    assert client.post("/billing/checkout", json={"plan": "free"}).status_code == 400
    assert client.post("/billing/checkout", json={"plan": "gold"}).status_code == 400
    assert client.post("/billing/portal").status_code == 409  # no customer yet


def test_a_completed_checkout_upgrades_the_account(client):
    STRIPE_STUB["subs"] = {"sub_1": subscription(client.user_id, price="price_pro")}
    r = webhook(client, "checkout.session.completed",
                {"mode": "subscription", "subscription": "sub_1", "client_reference_id": str(client.user_id)})
    assert r.status_code == 200 and "-> pro (active)" in r.json()["detail"]

    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "pro" and info["remaining"] == 40 and info["status"] == "active"
    assert info["can_manage"] is True and info["current_period_end"].startswith("2026-11-19")
    for _ in range(4):  # past the free limit now
        assert client.post("/jobs", json={"url": URL}).status_code == 202

    # the portal now works, with the stored customer id and a return URL
    assert client.post("/billing/portal").json()["url"] == "https://portal.stripe.test/ps_1"
    assert STRIPE_STUB["portals"] == [{"customer": "cus_9", "return_url": "https://app.example/billing"}]
    # and a second checkout reuses the customer instead of the email
    client.post("/billing/checkout", json={"plan": "starter"})
    assert STRIPE_STUB["checkouts"][-1]["customer"] == "cus_9"


def test_subscription_updates_change_the_plan_in_place(client):
    webhook(client, "customer.subscription.created", subscription(client.user_id, price="price_starter"))
    assert client.get("/billing/me").json()["plan"]["code"] == "starter"
    webhook(client, "customer.subscription.updated", subscription(client.user_id, price="price_pro"))
    assert client.get("/billing/me").json()["plan"]["code"] == "pro"
    with client.factory() as db:
        assert db.scalar(select(func.count()).select_from(Subscription)) == 1


def test_cancel_at_period_end_keeps_the_plan_then_deletion_falls_back(client):
    future = datetime.now(timezone.utc) + timedelta(days=10)
    webhook(client, "customer.subscription.updated", subscription(client.user_id, period_end=future, cancel_at_period_end=True))
    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "pro" and info["status"] == "cancelled"

    past = datetime.now(timezone.utc) - timedelta(days=1)
    webhook(client, "customer.subscription.deleted", subscription(client.user_id, status="canceled", period_end=past))
    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "free" and info["status"] == "cancelled"
    webhook(client, "customer.subscription.updated", subscription(client.user_id, status="past_due"))
    assert client.get("/billing/me").json()["plan"]["code"] == "pro"  # grace while Stripe retries the card


def test_bad_signature_unknown_user_and_unknown_price(client):
    assert webhook(client, "customer.subscription.created", subscription(client.user_id), secret="wrong").status_code == 400
    assert client.post("/billing/webhook/stripe", content=b"{}").status_code == 400  # no signature
    assert webhook(client, "customer.subscription.created", subscription(uuid.uuid4())).status_code == 400  # unknown user
    r = webhook(client, "customer.subscription.created", subscription(client.user_id, price="price_other"))
    assert r.status_code == 200 and "ignored price" in r.json()["detail"]
    r = webhook(client, "invoice.paid", {"id": "in_1"})
    assert r.status_code == 200 and "ignored event" in r.json()["detail"]
    r = webhook(client, "checkout.session.completed", {"mode": "payment", "subscription": None})
    assert r.status_code == 200 and "ignored checkout" in r.json()["detail"]
    with client.factory() as db:
        assert db.scalar(select(func.count()).select_from(Subscription)) == 0
    assert client.get("/billing/me").json()["plan"]["code"] == "free"


def test_webhook_and_checkout_are_503_when_billing_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", None)
    assert webhook(client, "customer.subscription.created", subscription(client.user_id)).status_code == 503
    assert client.post("/billing/checkout", json={"plan": "pro"}).status_code == 503
    assert client.get("/billing/me").json()["payments_enabled"] is False


def test_plan_for_edge_cases(monkeypatch):
    monkeypatch.setattr(settings, "default_plan", "free")
    assert billing.plan_for(None).code == "free"
    sub = Subscription(plan="pro", status="past_due", provider="x", provider_subscription_id="1")
    assert billing.plan_for(sub).code == "pro"  # grace period while the provider retries the card
    sub.status, sub.current_period_end = "cancelled", None
    assert billing.plan_for(sub).code == "free"
    sub.plan, sub.status = "unknown-plan", "active"
    assert billing.plan_for(sub).code == "free"


# ----------------------------------------------------------------- account deletion

def test_deleting_the_account_removes_everything_it_owns(client, tmp_path):
    webhook(client, "customer.subscription.created", subscription(client.user_id))
    job_id = uuid.UUID(client.post("/jobs", json={"url": URL}).json()["id"])
    with client.factory() as db:
        db_crud.mark_job_running(db, job_id)
        page = db_crud.create_page(db, job_id, url=URL, title="Home")
        db_crud.create_scenarios(db, job_id, page.id, [{"title": "t", "steps": ["s"], "expected_result": "r"}])
        db_crud.create_llm_call(db, job_id, page_id=page.id, agent="designer", model="m", attempt=1, outcome="ok",
                                input_tokens=1, output_tokens=1, cost_usd=0)
        db_crud.mark_job_done(db, job_id)
    report = Path(settings.reports_dir) / f"{job_id}.xlsx"
    shots = Path(settings.reports_dir) / str(job_id) / "screenshots"
    shots.mkdir(parents=True)
    report.write_bytes(b"x")
    (shots / "home.png").write_bytes(b"x")

    wrong = client.request("DELETE", "/auth/me", json={"password": "not-the-password"})
    assert wrong.status_code == 403  # not 401: the session itself is fine
    gone = client.request("DELETE", "/auth/me", json={"password": "correct-horse-battery"})
    assert gone.status_code == 204

    assert client.get("/auth/me").status_code == 401  # the token is dead with the account
    assert client.post("/auth/login", json={"email": "tester@example.com", "password": "correct-horse-battery"}).status_code == 401
    with client.factory() as db:
        for model in (User, Job, Page, Scenario, LlmCall, EmailToken, Subscription):
            assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    assert not report.exists() and not shots.parent.exists()


def test_deleting_a_job_removes_its_files_too(client):
    job_id = client.post("/jobs", json={"url": URL}).json()["id"]
    report = Path(settings.reports_dir) / f"{job_id}.xlsx"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"x")
    assert client.delete(f"/jobs/{job_id}").status_code == 204
    assert not report.exists()


# ----------------------------------------------------------------- retention

def test_retention_deletes_old_finished_jobs_only(client):
    ids = [uuid.UUID(client.post("/jobs", json={"url": URL}).json()["id"]) for _ in range(3)]
    with client.factory() as db:
        db_crud.mark_job_done(db, ids[0])
        db_crud.mark_job_failed(db, ids[1], "boom")
        for job_id in ids[:2]:
            db.get(Job, job_id).finished_at = datetime.now(timezone.utc) - timedelta(days=100)
        db.commit()
    old_report = Path(settings.reports_dir) / f"{ids[0]}.xlsx"
    old_report.parent.mkdir(parents=True, exist_ok=True)
    old_report.write_bytes(b"x")

    with client.factory() as db:
        assert cleanup.purge_expired_jobs(db, settings.reports_dir, retention_days=0) == 0  # disabled
        assert cleanup.purge_expired_jobs(db, settings.reports_dir, retention_days=90) == 2
        assert cleanup.purge_expired_jobs(db, settings.reports_dir, retention_days=90) == 0
        remaining = [j.id for j in db_crud.list_jobs(db)]
    assert remaining == [ids[2]] and not old_report.exists()  # the queued one stays
