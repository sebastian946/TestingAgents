"""Authenticated crawls (WTA-29): credentials handling, the deterministic login, and the
guardrails that apply once the crawler acts as a logged-in user.

Three layers:
- `security.credentials`: encrypt / decrypt / missing key.
- The API: credentials are encrypted at rest, never returned, refused without a key or
  for another host; the graph clears them right after the crawl.
- The browser: a local site with a real cookie-based login, crawled with Chromium. Correct
  credentials reach the private pages; wrong ones fall back to an anonymous crawl; action
  links (logout, delete) are not followed while logged in.
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents import explorer, graph
from agents.explorer import CrawledPage, LoginResult, _looks_unsafe, crawl
from config.variables import settings
from db import db_crud
from db.conn import Base, get_db
from db.models_db.models_db import Job, JobStatus
from main import app
from routes import routes
from security import credentials
from security.credentials import LoginSpec
from tests.conftest import sign_in

KEY = "u0cZQ6bQ6o9aRtd8cP9yq1lJ5jx0X2uY1hL1gmB2v0E="  # a valid Fernet key for tests only
URL = "https://shop.example/"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(settings, "credentials_key", KEY)


# ----------------------------------------------------------------- credentials module

def test_encrypt_round_trip_and_no_password_in_repr(with_key):
    spec = LoginSpec("https://shop.example/login", "qa@example.com", "s3cret-pass")
    blob = credentials.encrypt(spec)
    assert "s3cret-pass" not in blob and "qa@example.com" not in blob
    assert credentials.decrypt(blob) == spec
    assert "s3cret-pass" not in repr(spec) and "***" in repr(spec)


def test_missing_or_wrong_key(monkeypatch, with_key):
    blob = credentials.encrypt(LoginSpec(URL, "u", "p"))
    monkeypatch.setattr(settings, "credentials_key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with pytest.raises(credentials.CredentialsError, match="decrypted"):
        credentials.decrypt(blob)
    monkeypatch.setattr(settings, "credentials_key", None)
    assert not credentials.configured()
    with pytest.raises(credentials.CredentialsError, match="CREDENTIALS_KEY"):
        credentials.encrypt(LoginSpec(URL, "u", "p"))


# ----------------------------------------------------------------- API

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
    client.user_id = sign_in(client)
    yield client
    app.dependency_overrides.clear()
    engine.dispose()


LOGIN = {"login_url": f"{URL}login", "username": "qa", "password": "hunter22", "authorized": True}


def test_credentials_are_encrypted_at_rest_and_never_returned(client, with_key):
    created = client.post("/jobs", json={"url": URL, "login": LOGIN})
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["login_url"] == f"{URL}login"
    assert "hunter22" not in created.text and "login_credentials" not in body and "password" not in body

    with client.factory() as db:
        job = db.get(Job, uuid.UUID(body["id"]))
        assert job.login_credentials and "hunter22" not in job.login_credentials
        assert credentials.decrypt(job.login_credentials).password == "hunter22"
    detail = client.get(f"/jobs/{body['id']}")
    assert "hunter22" not in detail.text and "login_credentials" not in detail.json()


def test_login_is_refused_without_a_key_without_consent_or_on_another_host(client, monkeypatch):
    monkeypatch.setattr(settings, "credentials_key", None)
    r = client.post("/jobs", json={"url": URL, "login": LOGIN})
    assert r.status_code == 503 and "CREDENTIALS_KEY" in r.json()["detail"]

    monkeypatch.setattr(settings, "credentials_key", KEY)
    r = client.post("/jobs", json={"url": URL, "login": {**LOGIN, "authorized": False}})
    assert r.status_code == 400 and "authorized" in r.json()["detail"]
    r = client.post("/jobs", json={"url": URL, "login": {**LOGIN, "login_url": "https://other.example/login"}})
    assert r.status_code == 400 and "same site" in r.json()["detail"]
    r = client.post("/jobs", json={"url": URL, "login": {**LOGIN, "login_url": None}})
    assert r.status_code == 202 and r.json()["login_url"] == URL  # defaults to the job URL
    assert client.post("/jobs", json={"url": URL}).status_code == 202  # no login still fine without consent


def test_the_explorer_node_uses_and_then_forgets_the_credentials(client, with_key, fake_designer, monkeypatch, tmp_path):
    """The secret reaches `crawl` as a LoginSpec, never the state, and is gone right after."""
    for module in (graph,):
        monkeypatch.setattr(module, "local_session", client.factory)
    monkeypatch.setattr(graph.settings, "reports_dir", str(tmp_path / "reports"))
    seen = {}

    def fake_crawl(url, on_page=None, login=None, on_login=None, **_kwargs):
        seen["login"] = login
        with client.factory() as db:  # still present while the browser would be running
            seen["blob_during_crawl"] = db.get(Job, job_uuid).login_credentials
        on_login(LoginResult(False, "Login failed: the login form is still shown (wrong credentials?)."))
        page = CrawledPage(url=f"{url}home", title="Home", status_code=200, depth=0, page_type="content")
        on_page(page)
        return [page]

    monkeypatch.setattr(graph, "crawl", fake_crawl)
    job_id = client.post("/jobs", json={"url": URL, "login": LOGIN}).json()["id"]
    job_uuid = uuid.UUID(job_id)
    with client.factory() as db:
        db_crud.mark_job_running(db, job_uuid)

    updates = list(graph.testing_graph.stream(
        {"job_id": job_id, "url": URL, "description": None, "language": "en"}, stream_mode="updates"))

    assert seen["login"] == LoginSpec(f"{URL}login", "qa", "hunter22")
    assert seen["blob_during_crawl"] is not None
    with client.factory() as db:
        job = db.get(Job, job_uuid)
        assert job.login_credentials is None and job.status is JobStatus.DONE
        assert "wrong credentials" in job.error and "anonymous visitor" in job.error
    # the state carried only the note, never the secret
    assert updates[0]["explorer"]["login_note"].startswith("Login failed")
    assert "hunter22" not in str(updates)


def test_credentials_are_cleared_even_when_the_crawl_raises(client, with_key, monkeypatch, tmp_path):
    monkeypatch.setattr(graph, "local_session", client.factory)
    monkeypatch.setattr(graph.settings, "reports_dir", str(tmp_path / "reports"))

    def exploding_crawl(url, **_kwargs):
        raise RuntimeError("browser crashed")

    monkeypatch.setattr(graph, "crawl", exploding_crawl)
    job_id = client.post("/jobs", json={"url": URL, "login": LOGIN}).json()["id"]
    with pytest.raises(RuntimeError):
        graph.testing_graph.invoke({"job_id": job_id, "url": URL, "description": None, "language": "en"})
    with client.factory() as db:
        assert db.get(Job, uuid.UUID(job_id)).login_credentials is None


def test_finishing_a_job_and_the_startup_sweep_clear_credentials(client, with_key):
    ids = [client.post("/jobs", json={"url": URL, "login": LOGIN}).json()["id"] for _ in range(4)]
    with client.factory() as db:
        db_crud.mark_job_done(db, uuid.UUID(ids[0]))
        db_crud.mark_job_failed(db, uuid.UUID(ids[1]), "boom")
        old = db.get(Job, uuid.UUID(ids[2]))
        old.created_at = datetime.now(timezone.utc) - timedelta(hours=3)  # worker died mid-crawl
        db.commit()
        assert db.get(Job, uuid.UUID(ids[0])).login_credentials is None
        assert db.get(Job, uuid.UUID(ids[1])).login_credentials is None
        assert db_crud.clear_stale_credentials(db) == 1  # the old one; the fresh queued one keeps its secret
        assert db.get(Job, uuid.UUID(ids[2])).login_credentials is None
        assert db.get(Job, uuid.UUID(ids[3])).login_credentials is not None


# ----------------------------------------------------------------- unsafe links

@pytest.mark.parametrize("url, unsafe", [
    ("https://s.example/account/delete", True),
    ("https://s.example/logout", True),
    ("https://s.example/log-out", True),
    ("https://s.example/sign_out", True),
    ("https://s.example/checkout/step-1", True),
    ("https://s.example/orders/42/cancel", True),
    ("https://s.example/products", False),
    ("https://s.example/account/settings", False),
    ("https://s.example/blog/sending-emails-well", False),  # "sending" is not "send"
    ("https://s.example/orders", False),  # "orders" (a list) is not "order" (an action)
])
def test_looks_unsafe(url, unsafe):
    assert _looks_unsafe(url) is unsafe


# ----------------------------------------------------------------- the real login, in Chromium

SESSION = "sess-ok"

PUBLIC = {
    "/": "<title>Home</title><a href='/login'>Login</a><a href='/account'>Account</a><a href='/about'>About</a>",
    "/about": "<title>About</title>",
    "/login": """<title>Login</title>
        <form action="/session" method="post">
          <label>Email <input name="email" type="email"></label>
          <label>Password <input name="password" type="password"></label>
          <button type="submit">Sign in</button>
        </form>""",
}
PRIVATE = {
    "/account": "<title>My account</title><a href='/orders'>Orders</a><a href='/account/delete'>Delete account</a>"
                "<a href='/logout'>Log out</a>",
    "/orders": "<title>Orders</title>",
    "/account/delete": "<title>DELETED</title>",
    "/logout": "<title>Logged out</title>",
}


class SiteHandler(BaseHTTPRequestHandler):
    """A tiny site with a cookie session: /account and /orders need it, the rest is public."""

    hits: list[str] = []

    def log_message(self, *_):
        pass

    def _logged_in(self) -> bool:
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return SESSION in {m.value for m in jar.values()}

    def _send(self, status: int, body: str, extra: dict | None = None):
        data = f"<html><body>{body}</body></html>".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        SiteHandler.hits.append(path)
        if path in PUBLIC:
            self._send(200, PUBLIC[path])
        elif path in PRIVATE:
            self._send(200, PRIVATE[path]) if self._logged_in() else self._send(302, "", {"Location": "/login"})
        else:
            self._send(404, "nope")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = self.rfile.read(length).decode()
        if self.path == "/session" and "email=qa%40example.com" in form and "password=hunter22" in form:
            self._send(302, "", {"Location": "/account", "Set-Cookie": f"session={SESSION}; Path=/"})
        else:
            self._send(200, PUBLIC["/login"] + "<p>Wrong email or password</p>")


@pytest.fixture
def site():
    SiteHandler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with patch.object(explorer, "validate_public_url", lambda url: url):
        yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_a_correct_login_reaches_the_private_pages_and_skips_action_links(site):
    outcomes = []
    pages = crawl(site, max_pages=10, timeout=10, use_browser=True,
                  login=LoginSpec(f"{site}/login", "qa@example.com", "hunter22"), on_login=outcomes.append)

    assert outcomes and outcomes[0].ok and "qa@example.com" in outcomes[0].message
    paths = {p.url.removeprefix(site) for p in pages}
    assert {"/account", "/orders"} <= paths, paths  # behind the login
    assert "/account/delete" not in paths and "/logout" not in paths  # action links, not followed
    assert "/account/delete" not in SiteHandler.hits and "/logout" not in SiteHandler.hits  # never even requested
    assert "/about" in paths  # public pages still crawled


def test_a_wrong_password_falls_back_to_an_anonymous_crawl(site):
    outcomes = []
    pages = crawl(site, max_pages=10, timeout=10, use_browser=True,
                  login=LoginSpec(f"{site}/login", "qa@example.com", "wrong"), on_login=outcomes.append)

    assert outcomes and not outcomes[0].ok and "wrong credentials" in outcomes[0].message
    paths = {p.url.removeprefix(site) for p in pages}
    assert "/orders" not in paths  # redirected to /login, which is already crawled
    assert "/login" in paths and "/about" in paths


def test_a_login_page_without_a_form_is_reported(site):
    outcomes = []
    crawl(site, max_pages=3, timeout=10, use_browser=True,
          login=LoginSpec(f"{site}/about", "qa@example.com", "hunter22"), on_login=outcomes.append)
    assert not outcomes[0].ok and "No password field" in outcomes[0].message
