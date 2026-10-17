"""Email verification and password reset (WTA-28).

The API runs in-process on SQLite; the mailer is replaced by a recorder, so no email and no
network. The Redis enqueue is stubbed.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from config.variables import settings
from db import db_crud
from db.conn import Base, get_db
from db.models_db.models_db import EmailToken
from main import app
from routes import routes
from services import mailer
from tests.conftest import captured_emails, token_in

URL = "https://shop.example/"
CREDS = {"email": "ana@example.com", "password": "correct-horse-battery"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routes, "validate_public_url", lambda url: url)
    monkeypatch.setattr(routes, "add_new_job_to_queue", lambda job_id: None)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.factory = factory
    yield client
    app.dependency_overrides.clear()
    engine.dispose()


def register(client, creds=CREDS):
    """Register without verifying; returns the captured verification email."""
    with captured_emails() as outbox:
        response = client.post("/auth/register", json=creds)
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    assert len(outbox) == 1 and outbox[0].to == creds["email"].lower()
    return outbox[0]


# ------------------------------------------------------------------ verification

def test_register_sends_a_verification_link_and_jobs_wait_for_it(client):
    email = register(client)
    assert "Verify" in email.subject and f"{settings.app_url}/verify?token=" in email.text
    assert client.get("/auth/me").json()["email_verified"] is False

    blocked = client.post("/jobs", json={"url": URL})
    assert blocked.status_code == 403 and "Verify your email" in blocked.json()["detail"]
    assert client.get("/jobs").status_code == 200  # reading is fine, spending is not

    verified = client.post("/auth/verify", json={"token": token_in(email)})
    assert verified.status_code == 200 and verified.json()["email_verified"] is True
    assert client.get("/auth/me").json()["email_verified"] is True
    assert client.post("/jobs", json={"url": URL}).status_code == 202


def test_a_verification_token_works_once(client):
    email = register(client)
    assert client.post("/auth/verify", json={"token": token_in(email)}).status_code == 200
    again = client.post("/auth/verify", json={"token": token_in(email)})
    assert again.status_code == 400 and "invalid or has expired" in again.json()["detail"]


def test_unknown_and_expired_tokens_are_rejected(client):
    email = register(client)
    assert client.post("/auth/verify", json={"token": "x" * 43}).status_code == 400
    with client.factory() as db:
        token = db.scalars(select(EmailToken)).one()
        token.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert client.post("/auth/verify", json={"token": token_in(email)}).status_code == 400
    assert client.get("/auth/me").json()["email_verified"] is False


def test_requesting_a_new_link_invalidates_the_old_one(client):
    first = register(client)
    with captured_emails() as outbox:
        response = client.post("/auth/verify/request")
    assert response.status_code == 202 and len(outbox) == 1
    assert client.post("/auth/verify", json={"token": token_in(first)}).status_code == 400
    assert client.post("/auth/verify", json={"token": token_in(outbox[0])}).status_code == 200

    with captured_emails() as outbox:
        response = client.post("/auth/verify/request")
    assert response.status_code == 202 and "already verified" in response.json()["detail"]
    assert outbox == []  # nothing to send


def test_a_mail_provider_failure_does_not_break_registration(client, monkeypatch, capsys):
    def boom(email):
        raise mailer.MailError("provider down")

    monkeypatch.setattr(mailer, "send_email", boom)
    assert client.post("/auth/register", json=CREDS).status_code == 201
    assert "[mail] FAILED" in capsys.readouterr().out


def test_a_reset_token_cannot_verify_an_email(client):
    register(client)
    with captured_emails() as outbox:
        client.post("/auth/password/forgot", json={"email": CREDS["email"]})
    assert client.post("/auth/verify", json={"token": token_in(outbox[0])}).status_code == 400


# ---------------------------------------------------------------- password reset

def test_forgot_password_answers_the_same_for_unknown_emails(client):
    register(client)
    with captured_emails() as outbox:
        known = client.post("/auth/password/forgot", json={"email": "ANA@example.com"})
        unknown = client.post("/auth/password/forgot", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert [e.to for e in outbox] == ["ana@example.com"]  # only the real account got mail
    assert f"{settings.app_url}/reset-password?token=" in outbox[0].text


def test_reset_sets_the_new_password_and_logs_out_old_sessions(client):
    register(client)
    old_token = client.headers["Authorization"]
    with captured_emails() as outbox:
        client.post("/auth/password/forgot", json={"email": CREDS["email"]})

    reset = client.post("/auth/password/reset", json={"token": token_in(outbox[0]), "password": "new-password-123"})
    assert reset.status_code == 200
    body = reset.json()
    assert body["user"]["email_verified"] is True  # reading the email proves the address

    # The old session is gone, the new token works, and only the new password logs in
    assert client.get("/auth/me", headers={"Authorization": old_token}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}).status_code == 200
    assert client.post("/auth/login", json=CREDS).status_code == 401
    assert client.post("/auth/login", json={**CREDS, "password": "new-password-123"}).status_code == 200

    # and the reset link is spent
    again = client.post("/auth/password/reset", json={"token": token_in(outbox[0]), "password": "another-one-123"})
    assert again.status_code == 400


def test_reset_rejects_short_passwords_and_bad_tokens(client):
    register(client)
    with captured_emails() as outbox:
        client.post("/auth/password/forgot", json={"email": CREDS["email"]})
    assert client.post("/auth/password/reset", json={"token": token_in(outbox[0]), "password": "short"}).status_code == 422
    assert client.post("/auth/password/reset", json={"token": "y" * 43, "password": "long-enough-123"}).status_code == 400
    # the valid token was not consumed by the failed attempts
    assert client.post("/auth/password/reset", json={"token": token_in(outbox[0]), "password": "long-enough-123"}).status_code == 200


# ---------------------------------------------------------------------- mailer

def test_console_backend_prints_the_message(capsys, monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "console")
    mailer.send_email(mailer.Email(to="a@example.com", subject="Hi", text="link here", html="<p>x</p>"))
    out = capsys.readouterr().out
    assert "to=a@example.com" in out and "link here" in out


def test_resend_backend_posts_to_the_api(monkeypatch):
    monkeypatch.setattr(settings, "email_backend", "resend")
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    calls = []

    class Response:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(mailer.requests, "post", lambda url, **kw: calls.append((url, kw)) or Response())
    mailer.send_email(mailer.verification_email("a@example.com", "tok"))
    (url, kw), = calls
    assert url == mailer.RESEND_URL and kw["headers"]["Authorization"] == "Bearer re_test"
    assert kw["json"]["to"] == ["a@example.com"] and "verify?token=tok" in kw["json"]["text"]

    Response.status_code = 403
    with pytest.raises(mailer.MailError):
        mailer.send_email(mailer.verification_email("a@example.com", "tok"))

    monkeypatch.setattr(settings, "resend_api_key", None)
    with pytest.raises(mailer.MailError):
        mailer.send_email(mailer.verification_email("a@example.com", "tok"))
