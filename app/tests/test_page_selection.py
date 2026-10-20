"""WTA-32: explore first, let the user pick the pages, then design only those.

Graph level (fake crawl + fake designer on SQLite), API level (the select endpoint and the
report endpoint while waiting), and the worker's continuation task.
"""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents import graph
from agents.explorer import CrawledPage
from db import db_crud
from db.conn import Base, get_db
from db.models_db.models_db import Job, JobStatus, Page
from main import app
from routes import routes
from tests.conftest import sign_in
from worker import tasks

URL = "https://shop.example/"


@pytest.fixture
def env(tmp_path: Path, monkeypatch, fake_designer):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    for module in (tasks, graph):
        monkeypatch.setattr(module, "local_session", factory)
    monkeypatch.setattr(graph.settings, "reports_dir", str(tmp_path / "reports"))
    monkeypatch.setattr(routes, "validate_public_url", lambda url: url)
    enqueued: list[tuple[str, uuid.UUID]] = []
    monkeypatch.setattr(routes, "add_new_job_to_queue", lambda job_id: enqueued.append(("start", job_id)))
    monkeypatch.setattr(routes, "enqueue_continuation", lambda job_id: enqueued.append(("continue", job_id)))
    seen_max_pages: list[int] = []

    def crawl(url, max_pages=8, on_page=None, **_kwargs):
        seen_max_pages.append(max_pages)
        pages = [CrawledPage(url=f"{url}p{i}", title=f"P{i}", status_code=200, depth=min(i, 1), page_type="form")
                 for i in range(4)]
        for page in pages:
            on_page(page)
        return pages

    monkeypatch.setattr(graph, "crawl", crawl)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.user_id = sign_in(client)
    yield {"factory": factory, "client": client, "enqueued": enqueued, "max_pages": seen_max_pages, "designer": fake_designer}
    app.dependency_overrides.clear()
    engine.dispose()


def test_a_job_with_select_pages_pauses_after_exploring(env):
    client, factory = env["client"], env["factory"]
    created = client.post("/jobs", json={"url": URL, "select_pages": True}).json()
    assert created["select_pages"] is True and env["enqueued"] == [("start", uuid.UUID(created["id"]))]

    tasks.process_url_task(created["id"])  # what the worker does

    assert env["max_pages"] == [graph.settings.discovery_max_pages]  # wider crawl: no LLM cost
    job = client.get(f"/jobs/{created['id']}").json()
    assert job["status"] == "awaiting_selection" and job["pages_crawled"] == 4 and job["total_scenarios"] == 0
    assert all(p["selected"] for p in client.get(f"/jobs/{created['id']}/pages").json())

    report = client.get(f"/jobs/{created['id']}/report")
    assert report.status_code == 409 and report.json()["detail"]["status"] == "awaiting_selection"
    assert "choose" in report.json()["detail"]["message"]
    with factory() as db:
        assert db.get(Job, uuid.UUID(created["id"])).login_credentials is None


def test_selecting_pages_resumes_the_job_on_those_pages_only(env):
    client, factory = env["client"], env["factory"]
    job_id = client.post("/jobs", json={"url": URL, "select_pages": True}).json()["id"]
    tasks.process_url_task(job_id)
    pages = client.get(f"/jobs/{job_id}/pages").json()
    keep = [pages[0]["id"], pages[2]["id"]]

    resumed = client.post(f"/jobs/{job_id}/pages/select", json={"page_ids": keep})
    assert resumed.status_code == 202 and resumed.json()["status"] == "queued"
    assert env["enqueued"][-1] == ("continue", uuid.UUID(job_id))
    with factory() as db:
        flags = {p.id: p.selected for p in db_crud.get_pages_by_job(db, uuid.UUID(job_id))}
    assert [pid for pid, on in flags.items() if on] == keep

    tasks.continue_job_task(job_id)  # what the worker does next

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["pages_crawled"] == 4  # the unselected pages stay
    scenarios = client.get(f"/jobs/{job_id}/scenarios").json()
    assert {s["page_id"] for s in scenarios} == set(keep)
    assert job["total_scenarios"] == len(scenarios) > 0
    assert [s["scenario_code"] for s in scenarios][0] == "SC-001"
    assert client.get(f"/jobs/{job_id}/report").status_code == 200


def test_select_is_refused_unless_the_job_is_waiting(env):
    client = env["client"]
    job_id = client.post("/jobs", json={"url": URL}).json()["id"]  # a normal job
    r = client.post(f"/jobs/{job_id}/pages/select", json={"page_ids": [1]})
    assert r.status_code == 409 and "queued" in r.json()["detail"]

    paused = client.post("/jobs", json={"url": URL, "select_pages": True}).json()["id"]
    tasks.process_url_task(paused)
    r = client.post(f"/jobs/{paused}/pages/select", json={"page_ids": [999999]})
    assert r.status_code == 400 and "999999" in r.json()["detail"]
    assert client.post(f"/jobs/{paused}/pages/select", json={"page_ids": []}).status_code == 422
    assert client.get(f"/jobs/{paused}").json()["status"] == "awaiting_selection"  # untouched


def test_a_normal_job_still_designs_everything_without_pausing(env):
    client = env["client"]
    job_id = client.post("/jobs", json={"url": URL}).json()["id"]
    tasks.process_url_task(job_id)
    assert env["max_pages"] == [8]
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["select_pages"] is False
    assert {s["page_id"] for s in client.get(f"/jobs/{job_id}/scenarios").json()} == {
        p["id"] for p in client.get(f"/jobs/{job_id}/pages").json()
    }


def test_the_continuation_fails_cleanly_when_nothing_can_be_designed(env):
    client = env["client"]
    job_id = client.post("/jobs", json={"url": URL, "select_pages": True}).json()["id"]
    tasks.process_url_task(job_id)
    pages = client.get(f"/jobs/{job_id}/pages").json()
    env["designer"].fail_urls = {p["url"]: "declined" for p in pages}
    client.post(f"/jobs/{job_id}/pages/select", json={"page_ids": [pages[0]["id"]]})

    tasks.continue_job_task(job_id)

    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "failed" and "No scenarios could be designed" in job["error"]
    with env["factory"]() as db:
        assert len(db_crud.get_pages_by_job(db, uuid.UUID(job_id))) == 4  # pages kept for a retry


def test_continuation_graph_shape():
    drawn = graph.continuation_graph.get_graph()
    assert set(drawn.nodes) == {"__start__", "designer", "documenter", "fail", "__end__"}


def test_pages_default_to_selected(env):
    with env["factory"]() as db:
        job = db_crud.create_job(db, url=URL)
        page = db_crud.create_page(db, job.id, url=URL)
        assert db.get(Page, page.id).selected is True
        assert db_crud.select_pages(db, job.id, []) == 0
        assert db_crud.select_pages(db, job.id, [page.id]) == 1
