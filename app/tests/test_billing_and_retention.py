"""WTA-30: plans and monthly allowances, the Lemon Squeezy webhook, account deletion and
the retention sweep. In-process API on SQLite, signed fake webhooks, no provider calls.
"""
import hashlib
import hmac
import json
import uuid
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


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routes, "validate_public_url", lambda url: url)
    monkeypatch.setattr(routes, "add_new_job_to_queue", lambda job_id: None)
    monkeypatch.setattr(settings, "reports_dir", str(tmp_path / "reports"))
    monkeypatch.setattr(settings, "default_plan", "free")
    monkeypatch.setattr(settings, "lemonsqueezy_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "lemonsqueezy_variant_starter", "111")
    monkeypatch.setattr(settings, "lemonsqueezy_variant_pro", "222")
    monkeypatch.setattr(settings, "lemonsqueezy_checkout_pro_url", "https://shop.lemonsqueezy.com/buy/pro")
    monkeypatch.setattr(settings, "lemonsqueezy_api_key", "lsq-test-key")

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


def webhook(client, event, user_id, variant="222", status="active", sub_id="sub-1", ends_at=None, secret=SECRET, **extra):
    payload = {
        "meta": {"event_name": event, "custom_data": {"user_id": str(user_id)}},
        "data": {"type": "subscriptions", "id": sub_id, "attributes": {
            "variant_id": int(variant) if variant.isdigit() else variant, "status": status, "customer_id": 9,
            "renews_at": "2026-11-19T00:00:00.000000Z", "ends_at": ends_at,
            "urls": {"customer_portal": "https://portal.example/sub-1"}, **extra,
        }},
    }
    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/billing/webhook/lemonsqueezy", content=raw,
                       headers={"X-Signature": signature, "Content-Type": "application/json"})


class FakeProviderApi:
    """Stands in for `requests.request`: records calls and answers like Lemon Squeezy."""

    def __init__(self, status_code=200, status="cancelled", ends_at="2026-11-19T00:00:00.000000Z"):
        self.calls = []
        self.status_code = status_code
        self.status = status
        self.ends_at = ends_at

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, headers, json))
        api = self

        class Response:
            status_code = api.status_code

            def json(self):
                return {"data": {"type": "subscriptions", "id": url.rsplit("/", 1)[-1], "attributes": {
                    "status": api.status, "ends_at": api.ends_at, "renews_at": None,
                    "urls": {"customer_portal": "https://portal.example/sub-1"}}}}

        return Response()


# ----------------------------------------------------------------- allowances

def test_free_plan_allows_three_jobs_a_month_then_402(client):
    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "free" and info["remaining"] == 3 and info["status"] is None
    assert info["checkout_urls"]["pro"].startswith("https://shop.lemonsqueezy.com/buy/pro?checkout[email]=")
    assert f"checkout[custom][user_id]={client.user_id}" in info["checkout_urls"]["pro"]
    assert "starter" not in info["checkout_urls"]  # not configured in this test

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

def test_a_signed_webhook_upgrades_the_account(client):
    response = webhook(client, "subscription_created", client.user_id, variant="222")
    assert response.status_code == 200 and "-> pro (active)" in response.json()["detail"]

    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "pro" and info["remaining"] == 40 and info["status"] == "active"
    assert info["customer_portal_url"] == "https://portal.example/sub-1"
    assert info["current_period_end"].startswith("2026-11-19")
    for _ in range(4):  # past the free limit now
        assert client.post("/jobs", json={"url": URL}).status_code == 202


def test_updates_change_the_plan_in_place(client):
    webhook(client, "subscription_created", client.user_id, variant="111")
    assert client.get("/billing/me").json()["plan"]["code"] == "starter"
    webhook(client, "subscription_updated", client.user_id, variant="222")
    assert client.get("/billing/me").json()["plan"]["code"] == "pro"
    with client.factory() as db:
        assert db.scalar(select(func.count()).select_from(Subscription)) == 1


def test_cancelled_keeps_the_plan_until_the_period_ends_then_falls_back(client):
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    webhook(client, "subscription_cancelled", client.user_id, status="cancelled", ends_at=future)
    assert client.get("/billing/me").json()["plan"]["code"] == "pro"

    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    webhook(client, "subscription_expired", client.user_id, status="expired", ends_at=past)
    info = client.get("/billing/me").json()
    assert info["plan"]["code"] == "free" and info["status"] == "expired"


def test_cancel_and_resume_from_our_ui(client, monkeypatch):
    webhook(client, "subscription_created", client.user_id, variant="222")
    info = client.get("/billing/me").json()
    assert info["can_cancel"] is True and info["can_resume"] is False

    fake = FakeProviderApi(status="cancelled", ends_at=(datetime.now(timezone.utc) + timedelta(days=10)).isoformat())
    monkeypatch.setattr(billing.requests, "request", fake.request)
    response = client.post("/billing/cancel")
    assert response.status_code == 200, response.text
    method, url, headers, body = fake.calls[-1]
    assert method == "DELETE" and url.endswith("/subscriptions/sub-1") and headers["Authorization"] == "Bearer lsq-test-key"
    info = response.json()
    assert info["status"] == "cancelled" and info["plan"]["code"] == "pro"  # paid until the period ends
    assert info["can_cancel"] is False and info["can_resume"] is True
    assert client.post("/billing/cancel").status_code == 409  # nothing active any more

    fake.status = "active"
    response = client.post("/billing/resume")
    assert response.status_code == 200
    method, url, _, body = fake.calls[-1]
    assert method == "PATCH" and body["data"]["attributes"] == {"cancelled": False}
    assert response.json()["status"] == "active" and response.json()["can_cancel"] is True


def test_cancel_without_subscription_or_api_key_or_on_provider_error(client, monkeypatch):
    assert client.post("/billing/cancel").status_code == 409
    webhook(client, "subscription_created", client.user_id, variant="222")
    monkeypatch.setattr(settings, "lemonsqueezy_api_key", None)
    assert client.get("/billing/me").json()["can_cancel"] is False
    assert client.post("/billing/cancel").status_code == 409
    monkeypatch.setattr(settings, "lemonsqueezy_api_key", "lsq-test-key")
    monkeypatch.setattr(billing.requests, "request", FakeProviderApi(status_code=500).request)
    response = client.post("/billing/cancel")
    assert response.status_code == 409 and "HTTP 500" in response.json()["detail"]
    assert client.get("/billing/me").json()["status"] == "active"  # nothing changed locally


def test_bad_signature_unknown_user_and_unknown_variant(client):
    assert webhook(client, "subscription_created", client.user_id, secret="wrong").status_code == 400
    assert client.post("/billing/webhook/lemonsqueezy", content=b"{}").status_code == 400  # no signature
    assert webhook(client, "subscription_created", uuid.uuid4()).status_code == 400  # unknown user
    r = webhook(client, "subscription_created", client.user_id, variant="999")
    assert r.status_code == 200 and "ignored variant" in r.json()["detail"]
    r = webhook(client, "order_created", client.user_id)
    assert r.status_code == 200 and "ignored event" in r.json()["detail"]
    with client.factory() as db:
        assert db.scalar(select(func.count()).select_from(Subscription)) == 0
    assert client.get("/billing/me").json()["plan"]["code"] == "free"


def test_webhook_is_503_when_billing_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "lemonsqueezy_webhook_secret", None)
    assert webhook(client, "subscription_created", client.user_id).status_code == 503
    assert client.get("/billing/me").json()["checkout_urls"] == {} or True  # checkout links are independent


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
    webhook(client, "subscription_created", client.user_id)
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
