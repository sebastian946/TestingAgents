"""Acceptance tests for WTA-21: the job runs through the LangGraph StateGraph.

Fake crawl and fake Designer (conftest.py), real graph, real report, SQLite file. No
browser, network or LLM.
"""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents import graph
from agents.explorer import CrawledPage
from db import db_crud
from db.conn import Base
from db.models_db.models_db import Job, JobStatus
from worker import tasks

URL = "https://shop.example/"

# What each node may write (agents/state.py). Checked on every update the graph emits.
OWNED_KEYS = {
    "explorer": {"pages", "login_note"},
    "designer": {"scenarios_designed", "pages_without_scenarios"},
    "documenter": {"report_path", "status", "error"},
    "fail": {"status", "error"},
}


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch, fake_designer):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(tasks, "local_session", factory)
    monkeypatch.setattr(graph, "local_session", factory)
    monkeypatch.setattr(graph.settings, "reports_dir", str(tmp_path / "reports"))
    yield factory
    engine.dispose()


def _crawl_returning(n_pages: int):
    def crawl(url, on_page=None, **_kwargs):
        pages = [CrawledPage(url=f"{url}p{i}", title=f"P{i}", status_code=200, depth=min(i, 1), page_type="form")
                 for i in range(n_pages)]
        for page in pages:
            on_page(page)
        return pages
    return crawl


def _job(factory, description=None) -> str:
    with factory() as db:
        job_id = db_crud.create_job(db, url=URL, description=description).id
        db_crud.mark_job_running(db, job_id)
    return str(job_id)


def _initial(job_id, description=None):
    return {"job_id": job_id, "url": URL, "description": description}


def test_the_graph_has_the_designed_shape():
    drawn = graph.testing_graph.get_graph()
    assert set(drawn.nodes) == {"__start__", "explorer", "designer", "documenter", "fail", "__end__"}
    edges = {(e.source, e.target, e.conditional) for e in drawn.edges}
    assert ("__start__", "explorer", False) in edges
    assert {("explorer", "designer", True), ("explorer", "fail", True)} <= edges  # conditional edge 1
    assert {("designer", "documenter", True), ("designer", "fail", True)} <= edges  # conditional edge 2
    assert {("documenter", "__end__", False), ("fail", "__end__", False)} <= edges


def test_a_full_job_runs_url_to_pages_to_scenarios_to_excel(sessions, fake_designer, monkeypatch, tmp_path):
    monkeypatch.setattr(graph, "crawl", _crawl_returning(3))
    job_id = _job(sessions, description="A shop")

    final = graph.testing_graph.invoke(_initial(job_id, "A shop"))

    assert final["status"] == "done" and final["error"] is None
    assert [p["url"] for p in final["pages"]] == [f"{URL}p{i}" for i in range(3)]
    assert final["scenarios_designed"] == 6 and final["pages_without_scenarios"] == []
    assert Path(final["report_path"]).exists()
    assert fake_designer.descriptions == ["A shop"]  # the description reached the designer
    with sessions() as db:
        job = db.get(Job, uuid.UUID(job_id))
        assert job.status is JobStatus.DONE and job.report_path == final["report_path"]


def test_no_pages_ends_failed_through_the_conditional_edge_not_an_exception(sessions, fake_designer, monkeypatch):
    monkeypatch.setattr(graph, "crawl", _crawl_returning(0))
    job_id = _job(sessions)

    visited = [node for update in graph.testing_graph.stream(_initial(job_id), stream_mode="updates") for node in update]

    assert visited == ["explorer", "fail"]  # designer and documenter never ran
    assert fake_designer.descriptions == []
    with sessions() as db:
        job = db.get(Job, uuid.UUID(job_id))
        assert job.status is JobStatus.FAILED and job.error == graph.NO_PAGES_ERROR


def test_worker_task_with_no_pages_returns_normally_and_the_job_is_failed(sessions, fake_designer, monkeypatch):
    monkeypatch.setattr(graph, "crawl", _crawl_returning(0))
    with sessions() as db:
        job_id = db_crud.create_job(db, url=URL).id

    tasks.process_url_task(str(job_id))  # no exception: "nothing to work with" is a graph path

    with sessions() as db:
        assert db.get(Job, job_id).status is JobStatus.FAILED


def test_no_scenarios_ends_failed_through_the_second_conditional_edge(sessions, fake_designer, monkeypatch):
    monkeypatch.setattr(graph, "crawl", _crawl_returning(2))
    fake_designer.fail_urls = {f"{URL}p0": "declined", f"{URL}p1": "declined"}
    job_id = _job(sessions)

    visited = [node for update in graph.testing_graph.stream(_initial(job_id), stream_mode="updates") for node in update]

    assert visited == ["explorer", "designer", "fail"]
    with sessions() as db:
        job = db.get(Job, uuid.UUID(job_id))
        assert job.status is JobStatus.FAILED and "First error: declined" in job.error


def test_each_node_writes_only_its_own_part_of_the_state(sessions, fake_designer, monkeypatch):
    monkeypatch.setattr(graph, "crawl", _crawl_returning(2))
    fake_designer.fail_urls = {f"{URL}p1": "declined"}
    job_id = _job(sessions)

    updates = list(graph.testing_graph.stream(_initial(job_id), stream_mode="updates"))

    assert [next(iter(u)) for u in updates] == ["explorer", "designer", "documenter"]
    for update in updates:
        (node, written), = update.items()
        assert set(written) <= OWNED_KEYS[node], f"{node} wrote {set(written) - OWNED_KEYS[node]}"
    documenter_update = updates[-1]["documenter"]
    assert "1 of 2 pages" in documenter_update["error"]  # a done job that lists its gaps


def test_the_state_stays_plain_data(sessions, fake_designer, monkeypatch):
    """No ORM objects, sessions or browsers in the state: it must be JSON-serializable."""
    import json

    monkeypatch.setattr(graph, "crawl", _crawl_returning(1))
    final = graph.testing_graph.invoke(_initial(_job(sessions)))
    json.dumps(final)  # raises if anything non-serializable slipped in
