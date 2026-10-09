"""Acceptance tests for WTA-18: every LLM call is logged with its tokens and cost.

Designer-level tests use a fake Anthropic client; worker-level tests run `process_url_task`
against a SQLite file with the fake crawl and the fake Designer from conftest.py. No test
reaches the network.
"""
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agents import designer
from agents.designer import DesignerError, design_scenarios, estimate_cost_usd
from agents.designer_eval import LOGIN
from agents.explorer import CrawledPage
from db import db_crud
from db.conn import Base, get_db
from db.models_db.models_db import Job, LlmCall, Page
from main import app
from models.agent_models import DesignerOutput, TestScenario
from worker import tasks

URL = "https://shop.example/"


# --------------------------------------------------------------- pricing and Designer

def test_cost_uses_the_model_price_and_cache_multipliers():
    # Opus 5.5: $4 in / $20 out per MTok; cache writes 1.25x, reads 0.1x of the input price
    cost = estimate_cost_usd("claude-opus-5-5", 2_000, 1_000, cache_read_input_tokens=10_000, cache_creation_input_tokens=4_000)
    assert cost == pytest.approx((2_000 + 1.25 * 4_000 + 0.1 * 10_000) * 4 / 1e6 + 1_000 * 20 / 1e6)
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(1 + 5)
    assert estimate_cost_usd("claude-unknown", 1_000, 1_000) is None  # unknown price, not "free"


def _response(text, stop_reason="end_turn", model="claude-opus-5-5", tokens=(1200, 800)):
    usage = SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1], cache_read_input_tokens=None, cache_creation_input_tokens=0)
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)], model=model, usage=usage)


class ScriptedClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: self._responses.pop(0)))


VALID = DesignerOutput(scenarios=[TestScenario(
    title="Login works", steps=["Log in"], expected_result="Home page", priority="critical", category="functional",
)]).model_dump_json()


def test_every_call_is_reported_including_the_invalid_first_attempt():
    calls = []
    design_scenarios(LOGIN, client=ScriptedClient(_response("not json"), _response(VALID, tokens=(1500, 900))),
                     model="claude-opus-5-5", on_call=calls.append)
    assert [(c.attempt, c.outcome) for c in calls] == [(1, "invalid"), (2, "ok")]
    assert [c.input_tokens for c in calls] == [1200, 1500]
    assert all(c.cost_usd and c.cost_usd > 0 for c in calls)


def test_a_refused_call_is_reported_before_the_page_error():
    calls = []
    with pytest.raises(DesignerError):
        design_scenarios(LOGIN, client=ScriptedClient(_response("", stop_reason="refusal")), on_call=calls.append)
    assert [c.outcome for c in calls] == ["refusal"]  # billed, so logged


def test_a_fallback_call_is_priced_with_the_model_that_served_it():
    calls = []
    design_scenarios(LOGIN, client=ScriptedClient(_response(VALID, model="claude-opus-4-8")),
                     model="claude-opus-5-5", on_call=calls.append)
    call = calls[0]
    assert (call.requested_model, call.model) == ("claude-opus-5-5", "claude-opus-4-8")
    assert call.cost_usd == pytest.approx(estimate_cost_usd("claude-opus-4-8", 1200, 800))


# ------------------------------------------------------------------------ worker + DB

@pytest.fixture
def sessions(tmp_path: Path, monkeypatch, fake_designer):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(tasks, "local_session", factory)
    monkeypatch.setattr(tasks.settings, "reports_dir", str(tmp_path / "reports"))

    def crawl(url, on_page=None, **_kwargs):
        pages = [CrawledPage(url=f"{url}p{i}", title=f"P{i}", status_code=200, depth=min(i, 1)) for i in range(3)]
        for page in pages:
            on_page(page)
        return pages

    monkeypatch.setattr(tasks, "crawl", crawl)
    yield factory
    engine.dispose()


def _run_job(factory) -> uuid.UUID:
    with factory() as db:
        job_id = db_crud.create_job(db, url=URL).id
    tasks.process_url_task(str(job_id))
    return job_id


def _usage(factory, job_id) -> dict:
    with factory() as db:
        return db_crud.get_job_usage(db, job_id)


def test_a_finished_job_answers_how_many_tokens_and_dollars(sessions, fake_designer):
    job_id = _run_job(sessions)
    usage = _usage(sessions, job_id)

    assert usage["calls"] == 3  # one per page
    assert (usage["input_tokens"], usage["output_tokens"]) == (3_000, 1_500)
    assert usage["cost_usd"] == pytest.approx(3 * estimate_cost_usd("claude-opus-5-5", 1_000, 500))
    assert usage["calls_without_price"] == 0
    assert [m["model"] for m in usage["by_model"]] == ["claude-opus-5-5"]

    with sessions() as db:
        rows = db.execute(
            select(LlmCall.agent, LlmCall.outcome, Page.url).join(Page, LlmCall.page_id == Page.id)
            .where(LlmCall.job_id == job_id).order_by(LlmCall.id)
        ).all()
    assert [(r.agent, r.outcome, r.url) for r in rows] == [("designer", "ok", f"{URL}p{i}") for i in range(3)]


def test_failed_pages_still_add_their_billed_calls(sessions, fake_designer):
    fake_designer.fail_urls = {f"{URL}p1": "failed validation"}
    job_id = _run_job(sessions)

    usage = _usage(sessions, job_id)
    assert usage["calls"] == 4  # 2 ok pages + 2 invalid attempts on the failed one
    with sessions() as db:
        outcomes = db.execute(
            select(LlmCall.outcome, func.count()).where(LlmCall.job_id == job_id).group_by(LlmCall.outcome)
        ).all()
    assert dict(outcomes) == {"ok": 2, "invalid": 2}


def test_two_jobs_can_be_compared_with_sql(sessions, fake_designer):
    cheap = _run_job(sessions)
    fake_designer.tokens_per_call = (4_000, 3_000)
    expensive = _run_job(sessions)

    with sessions() as db:
        rows = db.execute(
            select(LlmCall.job_id, func.sum(LlmCall.input_tokens + LlmCall.output_tokens).label("tokens"),
                   func.sum(LlmCall.cost_usd).label("cost_usd"))
            .group_by(LlmCall.job_id).order_by(func.sum(LlmCall.cost_usd).desc())
        ).all()
    assert [r.job_id for r in rows] == [expensive, cheap]
    assert rows[0].tokens == 3 * 7_000 and rows[1].tokens == 3 * 1_500
    assert rows[0].cost_usd > rows[1].cost_usd


def test_cost_is_visible_while_the_job_runs(sessions, fake_designer):
    with sessions() as db:
        job_id = db_crud.create_job(db, url=URL).id
    seen = []
    fake_designer.after_page = lambda: seen.append(_usage(sessions, job_id)["calls"])
    tasks.process_url_task(str(job_id))
    assert seen == [1, 2, 3]


def test_a_retried_job_keeps_the_cost_of_the_previous_run(sessions, fake_designer):
    job_id = _run_job(sessions)
    tasks.process_url_task(str(job_id))  # RQ retry / re-enqueue: pages are rebuilt

    usage = _usage(sessions, job_id)
    assert usage["calls"] == 6  # both runs were paid for
    with sessions() as db:
        unlinked = db.scalar(select(func.count()).select_from(LlmCall).where(LlmCall.job_id == job_id, LlmCall.page_id.is_(None)))
    assert unlinked == 3  # the first run's calls lost their (deleted) pages, not their cost


def test_deleting_a_job_deletes_its_llm_calls(sessions, fake_designer):
    job_id = _run_job(sessions)
    with sessions() as db:
        assert db_crud.delete_job(db, job_id)
        assert db.scalar(select(func.count()).select_from(LlmCall)) == 0


def test_usage_endpoint(sessions, fake_designer):
    job_id = _run_job(sessions)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        body = client.get(f"/jobs/{job_id}/usage").json()
        assert body["calls"] == 3 and body["output_tokens"] == 1_500
        assert body["cost_usd"] == pytest.approx(3 * estimate_cost_usd("claude-opus-5-5", 1_000, 500))
        assert body["by_model"][0]["model"] == "claude-opus-5-5"
        assert client.get(f"/jobs/{uuid.uuid4()}/usage").status_code == 404
    finally:
        app.dependency_overrides.clear()
