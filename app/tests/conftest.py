"""Shared fixtures. The worker tests replace the Designer so no test ever reaches the API."""
import itertools

import pytest

from agents.designer import CallUsage, DesignResult, PageDesign
from models.agent_models import TestScenario

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


@pytest.fixture
def fake_designer(monkeypatch) -> FakeDesigner:
    from worker import tasks

    fake = FakeDesigner()
    monkeypatch.setattr(tasks, "design_pages", fake)
    return fake
