"""Accounts and access control (WTA-26).

The API runs in-process on SQLite. No Redis (the enqueue is stubbed), no LLM, no network.
"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.variables import settings
from db import db_crud
from db.conn import Base, get_db
from db.models_db.models_db import Job
from main import app
from routes import routes
from security import auth
from tests.conftest import sign_in

URL = "https://shop.example/"
CREDENTIALS = {"email": "Ana@Example.com", "password": "correct-horse-battery"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(routes, "validate_public_url", lambda url: url)  # no DNS lookup
    monkeypatch.setattr(routes, "add_new_job_to_queue", lambda job_id: None)  # no Redis

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.factory = factory
    yield client
    app.dependency_overrides.clear()
    engine.dispose()


# ----------------------------------------------------------------- register / login

def test_register_returns_a_token_and_normalizes_the_email(client):
    response = client.post("/auth/register", json=CREDENTIALS)

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == settings.auth_token_expire_minutes * 60
    assert body["user"]["email"] == "ana@example.com"  # lower-cased: one account per mailbox
    assert "password" not in body["user"] and "password_hash" not in body["user"]
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    assert client.get("/auth/me").json()["email"] == "ana@example.com"


def test_the_password_is_stored_hashed(client):
    client.post("/auth/register", json=CREDENTIALS)
    with client.factory() as db:
        user = db_crud.get_user_by_email(db, CREDENTIALS["email"])
    assert user.password_hash.startswith("$argon2") and CREDENTIALS["password"] not in user.password_hash


def test_registering_the_same_email_twice_is_a_409(client):
    assert client.post("/auth/register", json=CREDENTIALS).status_code == 201
    again = client.post("/auth/register", json={**CREDENTIALS, "email": "ANA@example.com"})
    assert again.status_code == 409 and "already exists" in again.json()["detail"]


@pytest.mark.parametrize("payload", [
    {"email": "not-an-email", "password": "correct-horse-battery"},
    {"email": "ana@example.com", "password": "short"},
    {"email": "ana@example.com"},
])
def test_invalid_credentials_are_a_422(client, payload):
    assert client.post("/auth/register", json=payload).status_code == 422


def test_login_with_the_right_password(client):
    client.post("/auth/register", json=CREDENTIALS)
    response = client.post("/auth/login", json={"email": "ana@example.com", "password": CREDENTIALS["password"]})
    assert response.status_code == 200 and response.json()["user"]["email"] == "ana@example.com"


@pytest.mark.parametrize("email, password", [
    ("ana@example.com", "wrong-password"),
    ("nobody@example.com", "correct-horse-battery"),
])
def test_login_failures_do_not_say_which_field_was_wrong(client, email, password):
    client.post("/auth/register", json=CREDENTIALS)
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401 and response.json()["detail"] == "Wrong email or password."


# ----------------------------------------------------------------- tokens

def test_every_jobs_route_needs_a_token(client):
    for method, path in [("get", "/jobs"), ("post", "/jobs"), ("get", f"/jobs/{uuid.uuid4()}"),
                         ("get", f"/jobs/{uuid.uuid4()}/report"), ("delete", f"/jobs/{uuid.uuid4()}")]:
        response = client.request(method, path, json={"url": URL} if method == "post" else None)
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert client.get("/auth/me").status_code == 401
    assert client.get("/Health").status_code == 200  # public


def test_a_tampered_or_expired_token_is_rejected(client):
    user_id = sign_in(client)
    assert client.get("/auth/me").status_code == 200

    expired = auth.create_access_token(user_id, expires_minutes=-1)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    forged = jwt.encode({"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        "another-key", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_a_token_of_a_deleted_account_stops_working(client):
    user_id = sign_in(client)
    with client.factory() as db:
        db.delete(db_crud.get_user(db, user_id))
        db.commit()
    assert client.get("/auth/me").status_code == 401


# ----------------------------------------------------------------- ownership

def test_jobs_belong_to_the_user_who_created_them(client):
    user_id = sign_in(client)
    created = client.post("/jobs", json={"url": URL})
    assert created.status_code == 202
    job_id = created.json()["id"]
    with client.factory() as db:
        assert db.get(Job, uuid.UUID(job_id)).user_id == user_id
    assert [j["id"] for j in client.get("/jobs").json()] == [job_id]


def test_a_user_only_sees_their_own_jobs(client):
    sign_in(client, email="ana@example.com")
    mine = client.post("/jobs", json={"url": URL}).json()["id"]

    other = TestClient(app)
    sign_in(other, email="bob@example.com")
    theirs = other.post("/jobs", json={"url": URL}).json()["id"]

    assert [j["id"] for j in other.get("/jobs").json()] == [theirs]
    for path in (f"/jobs/{mine}", f"/jobs/{mine}/pages", f"/jobs/{mine}/scenarios", f"/jobs/{mine}/usage",
                 f"/jobs/{mine}/report"):
        assert other.get(path).status_code == 404, path
    assert other.delete(f"/jobs/{mine}").status_code == 404
    assert client.get(f"/jobs/{mine}").status_code == 200
    assert client.delete(f"/jobs/{mine}").status_code == 204


def test_jobs_created_before_accounts_existed_are_not_visible(client):
    sign_in(client)
    with client.factory() as db:
        old = db_crud.create_job(db, url=URL)  # user_id NULL
    assert client.get("/jobs").json() == []
    assert client.get(f"/jobs/{old.id}").status_code == 404


# ----------------------------------------------------------------- hourly limit

def test_the_hourly_job_limit_answers_429(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_max_jobs_per_hour", 2)
    sign_in(client)
    assert client.post("/jobs", json={"url": URL}).status_code == 202
    assert client.post("/jobs", json={"url": URL}).status_code == 202

    third = client.post("/jobs", json={"url": URL})
    assert third.status_code == 429 and third.headers["retry-after"] == "3600"
    assert "2 jobs per hour" in third.json()["detail"]

    # Jobs older than an hour no longer count
    with client.factory() as db:
        for job in db_crud.list_jobs(db):
            job.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
    assert client.post("/jobs", json={"url": URL}).status_code == 202


def test_the_limit_is_per_user(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_max_jobs_per_hour", 1)
    sign_in(client, email="ana@example.com")
    assert client.post("/jobs", json={"url": URL}).status_code == 202
    assert client.post("/jobs", json={"url": URL}).status_code == 429

    other = TestClient(app)
    sign_in(other, email="bob@example.com")
    assert other.post("/jobs", json={"url": URL}).status_code == 202
