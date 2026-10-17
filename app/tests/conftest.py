"""Shared fixtures for the whole suite.

- A safety net (autouse): no test can reach the real Anthropic API or LangSmith, whatever
  the developer's .env contains. A test that forgot to mock the LLM fails fast on a dead
  local address instead of spending tokens.
- Sample pages (`login_page_info`, `login_page`, `contact_page`): realistic inventories, as
  the explorer extracts them, for any test that needs a page to design.
- `fake_designer`: replaces `design_pages` in agents.graph for graph and worker tests.
"""
import os

# Before any app import: tests must never send traces to LangSmith, even when .env has
# LANGSMITH_TRACING=true (config.variables only fills variables that are not set yet)
os.environ["LANGSMITH_TRACING"] = "false"

FAKE_ANTHROPIC_KEY = "sk-ant-test-not-a-real-key"
# Port 9 ("discard") on localhost: nothing listens, so a request fails at once
DEAD_ANTHROPIC_URL = "http://127.0.0.1:9"

import contextlib  # noqa: E402
import itertools  # noqa: E402

import pytest  # noqa: E402

from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

from agents.designer import CallUsage, DesignResult, PageDesign  # noqa: E402
from agents.page_info import PageInfo  # noqa: E402
from models.agent_models import TestScenario  # noqa: E402


# --------------------------------------------------------------------------- safety net

@pytest.fixture(autouse=True)
def no_real_anthropic_api(monkeypatch):
    """Every test runs with a fake key and a dead API address, never the .env ones."""
    from config.variables import settings

    monkeypatch.setattr(settings, "anthropic_api_key", FAKE_ANTHROPIC_KEY)
    monkeypatch.setattr(settings, "designer_max_retries", 0)  # fail fast if a call slips through
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_ANTHROPIC_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", DEAD_ANTHROPIC_URL)


# ------------------------------------------------------------------------ sample pages

@dataclass
class SamplePage:
    """What the Designer receives for a page (url, title, page_type, elements)."""

    url: str
    title: str | None
    page_type: str | None
    elements: dict[str, Any] | None


# Inventory the explorer extracted from https://www.saucedemo.com/ (WTA-11)
LOGIN_EVALUATE = {
    "headings": [],
    "forms": [{
        "action": None, "method": "get", "submit_text": "Login", "virtual": False,
        "fields": [
            {"tag": "input", "type": "text", "name": "user-name", "id": "user-name", "label": "Username",
             "placeholder": "Username", "required": False, "options": []},
            {"tag": "input", "type": "password", "name": "password", "id": "password", "label": "Password",
             "placeholder": "Password", "required": False, "options": []},
        ],
    }],
    "buttons": [], "nav_links": [], "signals": {"word_count": 16},
}

CONTACT_EVALUATE = {
    "headings": ["Contact us"],
    "forms": [{
        "action": "https://shop.example/send", "method": "post", "submit_text": "Send message", "virtual": False,
        "fields": [
            {"tag": "input", "type": "text", "name": "name", "id": None, "label": "Your name",
             "placeholder": None, "required": True, "options": []},
            {"tag": "input", "type": "email", "name": "email", "id": None, "label": "Email",
             "placeholder": "you@example.com", "required": True, "options": []},
            {"tag": "textarea", "type": "textarea", "name": "message", "id": None, "label": "Message",
             "placeholder": None, "required": False, "options": []},
        ],
    }],
    "buttons": [], "nav_links": [{"text": "Home", "href": "https://shop.example/"}], "signals": {"word_count": 40},
}


@pytest.fixture
def login_page_info() -> PageInfo:
    """A real login page inventory, as a PageInfo object."""
    return PageInfo.from_evaluate("https://www.saucedemo.com/", "Swag Labs", LOGIN_EVALUATE)


@pytest.fixture
def login_page(login_page_info) -> SamplePage:
    """The login page as the Designer receives it."""
    return SamplePage("https://www.saucedemo.com/", "Swag Labs", "login", login_page_info.to_dict())


@pytest.fixture
def contact_page() -> SamplePage:
    info = PageInfo.from_evaluate("https://shop.example/contact", "Contact", CONTACT_EVALUATE)
    return SamplePage("https://shop.example/contact", "Contact", "form", info.to_dict())

PRIORITIES = ["critical", "high", "medium", "low"]
CATEGORIES = ["functional", "negative", "validation", "security"]


class FakeDesigner:
    """Stand-in for `agents.designer.design_pages` as the worker calls it.

    Each page gets `per_page` scenarios with rotating priorities and categories. Pages whose
    url is in `fail_urls` get the given error instead; `fatal` is raised before any page;
    `after_page` runs after each page is persisted (to observe progress from outside).
    Like the real one, it reports every API call through `on_call(page, usage)`: one `ok`
    call per designed page, two `invalid` calls (first try + self-healing retry) per failed
    page, each with `tokens_per_call` = (input, output).
    """

    MODEL = "claude-opus-5-5"

    def __init__(self):
        self.per_page = 2
        self.fail_urls: dict[str, str] = {}
        self.fatal: Exception | None = None
        self.after_page = None
        self.descriptions: list[str | None] = []
        self.languages: list[str | None] = []
        self.tokens_per_call = (1000, 500)
        self._rotation = itertools.count()

    def _call(self, attempt: int, outcome: str) -> CallUsage:
        tokens_in, tokens_out = self.tokens_per_call
        return CallUsage(self.MODEL, self.MODEL, attempt, outcome, tokens_in, tokens_out, 0, 0)

    def _scenarios(self, page) -> list[TestScenario]:
        out = []
        for i in range(self.per_page):
            n = next(self._rotation)
            out.append(TestScenario(
                title=f"{page.url} scenario {i + 1}",
                steps=["Open the page", f"Do step {i + 1}"],
                expected_result="The page responds as expected",
                priority=PRIORITIES[n % len(PRIORITIES)],
                category=CATEGORIES[n % len(CATEGORIES)],
            ))
        return out

    def __call__(self, pages, description=None, *, on_result=None, on_call=None, **_options):
        self.descriptions.append(description)
        self.languages.append(_options.get("language"))
        if self.fatal is not None:
            raise self.fatal
        outcomes = []
        for page in pages:
            if page.url in self.fail_urls:
                calls = [self._call(1, "invalid"), self._call(2, "invalid")]
                outcome = PageDesign(page, error=self.fail_urls[page.url])
            else:
                calls = [self._call(1, "ok")]
                result = DesignResult(self._scenarios(page), self.MODEL, 1, *self.tokens_per_call, 0, 0)
                outcome = PageDesign(page, result=result)
            if on_call is not None:
                for call in calls:
                    on_call(page, call)
            outcomes.append(outcome)
            if on_result is not None:
                on_result(outcome)
            if self.after_page is not None:
                self.after_page()
        return outcomes


# ---------------------------------------------------------------- auth (WTA-26)

def sign_in(client, email: str = "tester@example.com", password: str = "correct-horse-battery") -> "uuid.UUID":
    """Register an account through the API and make `client` send its token from now on.

    Every /jobs route needs a user, and a job is only visible to its owner: tests that
    create jobs straight in the database pass the returned id as `user_id`.
    """
    import uuid

    with captured_emails() as outbox:
        response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    # Creating jobs needs a verified email (WTA-28): click the link from the captured email
    verified = client.post("/auth/verify", json={"token": token_in(outbox[-1])})
    assert verified.status_code == 200, verified.text
    return uuid.UUID(response.json()["user"]["id"])


@contextlib.contextmanager
def captured_emails():
    """Replace the mailer for a block and collect what would have been sent."""
    from services import mailer

    outbox: list = []
    original = mailer.send_email
    mailer.send_email = outbox.append
    try:
        yield outbox
    finally:
        mailer.send_email = original


def token_in(email) -> str:
    """The token of the link inside an email produced by services.mailer."""
    import re

    match = re.search(r"token=([A-Za-z0-9_\-]+)", email.text)
    assert match, email.text
    return match.group(1)


@pytest.fixture
def fake_designer(monkeypatch) -> FakeDesigner:
    from agents import graph

    fake = FakeDesigner()
    monkeypatch.setattr(graph, "design_pages", fake)
    return fake
