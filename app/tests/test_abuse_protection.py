"""WTA-30: rate limits, leaked-password check, job retries and timeouts.

In-process API on SQLite; the limiter counts in memory (conftest); HIBP is replaced by a
fake `requests.get`; the RQ queue is inspected without Redis.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.variables import settings
from db.conn import Base, get_db
from db.redis import redis_conn
from main import app
from routes import routes
from security import passwords, ratelimit
from tests.conftest import sign_in
from worker import tasks

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
    yield client
    app.dependency_overrides.clear()
    engine.dispose()


# ----------------------------------------------------------------- rate limits

def test_login_is_limited_per_account(client):
    sign_in(client)
    client.headers.pop("Authorization")
    for _ in range(ratelimit.LOGIN_EMAIL.max_hits):
        assert client.post("/auth/login", json={**CREDS, "email": "tester@example.com", "password": "nope-nope"}).status_code == 401
    blocked = client.post("/auth/login", json={"email": "tester@example.com", "password": "correct-horse-battery"})
    assert blocked.status_code == 429
    assert "Try again" in blocked.json()["detail"] and int(blocked.headers["retry-after"]) > 0
    # another account from the same IP still works (the per-IP limit is higher)
    assert client.post("/auth/login", json={"email": "other@example.com", "password": "nope-nope"}).status_code == 401


def test_login_is_limited_per_ip_across_accounts(client):
    for i in range(ratelimit.LOGIN_IP.max_hits):
        assert client.post("/auth/login", json={"email": f"u{i}@example.com", "password": "nope-nope"}).status_code == 401
    assert client.post("/auth/login", json={"email": "new@example.com", "password": "nope-nope"}).status_code == 429


def test_forgot_password_is_limited_per_email(client):
    for _ in range(ratelimit.FORGOT_EMAIL.max_hits):
        assert client.post("/auth/password/forgot", json={"email": "ana@example.com"}).status_code == 202
    assert client.post("/auth/password/forgot", json={"email": "ana@example.com"}).status_code == 429
    assert client.post("/auth/password/forgot", json={"email": "bob@example.com"}).status_code == 202


def test_token_guessing_is_limited(client):
    for _ in range(ratelimit.TOKEN_IP.max_hits):
        assert client.post("/auth/verify", json={"token": "x" * 40}).status_code == 400
    assert client.post("/auth/verify", json={"token": "x" * 40}).status_code == 429
    assert client.post("/auth/password/reset", json={"token": "x" * 40, "password": "long-enough-123"}).status_code == 429


def test_register_is_limited_per_ip(client):
    for i in range(ratelimit.REGISTER_IP.max_hits):
        assert client.post("/auth/register", json={"email": f"r{i}@example.com", "password": "correct-horse-battery"}).status_code == 201
    assert client.post("/auth/register", json={"email": "late@example.com", "password": "correct-horse-battery"}).status_code == 429


def test_limits_can_be_disabled_and_fail_open_without_redis(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    for _ in range(ratelimit.LOGIN_IP.max_hits + 5):
        assert client.post("/auth/login", json={"email": "x@example.com", "password": "nope-nope"}).status_code == 401
    monkeypatch.setattr(settings, "rate_limit_enabled", True)

    from redis.exceptions import ConnectionError as RedisConnectionError

    class DeadRedis:
        def pipeline(self):
            raise RedisConnectionError("down")

    monkeypatch.setattr(ratelimit.limiter, "backend", ratelimit.RedisBackend(DeadRedis()))
    for _ in range(ratelimit.LOGIN_IP.max_hits + 5):
        assert client.post("/auth/login", json={"email": "x@example.com", "password": "nope-nope"}).status_code == 401


def test_redis_backend_counts_and_expires():
    """The Redis backend against a tiny fake with the same pipeline/incr/ttl/expire API."""

    class FakePipe:
        def __init__(self, store):
            self.store, self.ops = store, []

        def incr(self, key):
            self.ops.append(("incr", key))

        def ttl(self, key):
            self.ops.append(("ttl", key))

        def execute(self):
            out = []
            for op, key in self.ops:
                if op == "incr":
                    self.store[key] = self.store.get(key, (0, -1))[0] + 1, self.store.get(key, (0, -1))[1]
                    out.append(self.store[key][0])
                else:
                    out.append(self.store.get(key, (0, -1))[1])
            return out

    class FakeRedis:
        def __init__(self):
            self.store = {}

        def pipeline(self):
            return FakePipe(self.store)

        def expire(self, key, seconds):
            self.store[key] = (self.store[key][0], seconds)

    backend = ratelimit.RedisBackend(FakeRedis())
    assert backend.hit("k", 60) == (1, 60)  # first hit sets the window
    assert backend.hit("k", 60) == (2, 60)


# ----------------------------------------------------------------- leaked passwords

class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise passwords.requests.HTTPError(str(self.status_code))


def test_pwned_password_is_rejected_on_register_and_reset(client, monkeypatch):
    monkeypatch.setattr(settings, "pwned_passwords_check", True)
    import hashlib

    leaked = "password123"
    suffix = hashlib.sha1(leaked.encode()).hexdigest().upper()[5:]
    sent = {}

    def fake_get(url, **kw):
        sent["url"] = url
        return FakeResponse(f"0000000000000000000000000000000000A:0\n{suffix}:12345\nFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:3\n")

    monkeypatch.setattr(passwords.requests, "get", fake_get)
    r = client.post("/auth/register", json={"email": "ana@example.com", "password": leaked})
    assert r.status_code == 400 and "data breaches" in r.json()["detail"]
    assert sent["url"].endswith(hashlib.sha1(leaked.encode()).hexdigest().upper()[:5])  # only the prefix left the server
    assert leaked not in sent["url"]
    assert client.post("/auth/register", json={"email": "ana@example.com", "password": "something-unique-42"}).status_code == 201


def test_leak_check_fails_open_when_hibp_is_down(monkeypatch):
    monkeypatch.setattr(settings, "pwned_passwords_check", True)
    monkeypatch.setattr(passwords.requests, "get", lambda url, **kw: FakeResponse("", 503))
    assert passwords.is_pwned("password123") is False

    def boom(url, **kw):
        raise passwords.requests.ConnectionError("no network")

    monkeypatch.setattr(passwords.requests, "get", boom)
    assert passwords.is_pwned("password123") is False


def test_padding_entries_do_not_count_as_leaks(monkeypatch):
    monkeypatch.setattr(settings, "pwned_passwords_check", True)
    import hashlib

    suffix = hashlib.sha1(b"fine-password").hexdigest().upper()[5:]
    monkeypatch.setattr(passwords.requests, "get", lambda url, **kw: FakeResponse(f"{suffix}:0\n"))
    assert passwords.is_pwned("fine-password") is False


# ----------------------------------------------------------------- retries and timeouts

def test_jobs_are_enqueued_with_a_timeout_and_retries(monkeypatch):
    monkeypatch.setattr(settings, "job_timeout_seconds", 600)
    monkeypatch.setattr(settings, "job_max_retries", 2)
    options = redis_conn.enqueue_options()
    assert options["job_timeout"] == 600 and options["result_ttl"] == 0
    assert options["retry"].max == 2 and options["retry"].intervals == [30]

    monkeypatch.setattr(settings, "job_max_retries", 0)
    assert "retry" not in redis_conn.enqueue_options()


def test_failure_reason_mentions_remaining_retries_and_timeouts(monkeypatch):
    class CurrentJob:
        retries_left = 1

    monkeypatch.setattr(tasks, "get_current_job", lambda: CurrentJob())
    assert tasks._failure_reason(RuntimeError("browser crashed")) == "browser crashed Retrying automatically (1 attempt left)."

    class JobTimeoutException(Exception):
        pass

    monkeypatch.setattr(settings, "job_timeout_seconds", 900)
    CurrentJob.retries_left = 0
    assert tasks._failure_reason(JobTimeoutException("Task exceeded maximum timeout value (900 seconds)")) == \
        "The job exceeded the time limit of 15 minutes."

    monkeypatch.setattr(tasks, "get_current_job", lambda: None)
    assert tasks._failure_reason(RuntimeError("")) == "RuntimeError"
