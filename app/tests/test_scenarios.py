"""Acceptance tests for WTA-17: the worker designs each crawled page and persists its scenarios.

Runs `process_url_task` for real against a SQLite file with two connections (worker and
"API"). The crawl and the Designer are faked (see conftest.py), so no browser, network or
API key is involved. The SQL criteria use the same queries documented in the README.
"""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agents.designer import DesignerFatalError
from agents.explorer import CrawledPage
from db import db_crud
from db.conn import Base
from db.models_db.models_db import Job, JobStatus, Page, Scenario
from worker import tasks

URL = "https://shop.example/"


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(tasks, "local_session", factory)
    monkeypatch.setattr(tasks.settings, "reports_dir", str(tmp_path / "reports"))
    yield factory
    engine.dispose()


@pytest.fixture
def three_pages(monkeypatch):
    """Fake crawl that emits 3 pages through the real on_page callback."""
    def crawl(url, on_page=None, **_kwargs):
        pages = [CrawledPage(url=f"{url}p{i}", title=f"P{i}", status_code=200, depth=min(i, 1)) for i in range(3)]
        for page in pages:
            on_page(page)
        return pages
    monkeypatch.setattr(tasks, "crawl", crawl)


def _new_job(factory, description=None) -> uuid.UUID:
    with factory() as db:
        return db_crud.create_job(db, url=URL, description=description).id


def _job(factory, job_id) -> Job:
    with factory() as db:
        job = db.get(Job, job_id)
        db.expunge(job)
        return job


def _count(factory, job_id) -> int:
    with factory() as db:
        return db.scalar(select(func.count()).select_from(Scenario).where(Scenario.job_id == job_id))


def test_full_job_persists_scenarios_linked_to_their_pages(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    tasks.process_url_task(str(job_id))

    job = _job(sessions, job_id)
    assert job.status is JobStatus.DONE and job.error is None
    assert job.total_scenarios == _count(sessions, job_id) == 6  # counter == real count

    with sessions() as db:
        rows = db.execute(
            select(Scenario.scenario_code, Scenario.title, Scenario.steps, Page.url)
            .join(Page, Scenario.page_id == Page.id)
            .where(Scenario.job_id == job_id)
            .order_by(Scenario.id)
        ).all()
    codes = [r.scenario_code for r in rows]
    assert codes == [f"SC-{n:03d}" for n in range(1, 7)]  # readable, sequential across pages
    assert len(set(codes)) == len(codes)  # unique within the job
    assert all(r.title.startswith(r.url) for r in rows)  # each scenario is linked to its own page
    assert rows[0].steps == ["Open the page", "Do step 1"]  # steps stored as a JSON list


def test_scenarios_can_be_queried_by_page_priority_and_category(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    tasks.process_url_task(str(job_id))

    with sessions() as db:
        by_page = dict(db.execute(
            select(Page.url, func.count(Scenario.id)).join(Scenario, Scenario.page_id == Page.id)
            .where(Page.job_id == job_id).group_by(Page.url)
        ).all())
        by_priority = dict(db.execute(
            select(Scenario.priority, func.count()).where(Scenario.job_id == job_id).group_by(Scenario.priority)
        ).all())
        by_category = dict(db.execute(
            select(Scenario.category, func.count()).where(Scenario.job_id == job_id).group_by(Scenario.category)
        ).all())

    assert by_page == {f"{URL}p0": 2, f"{URL}p1": 2, f"{URL}p2": 2}
    assert sum(by_priority.values()) == 6 and set(by_priority) <= {"critical", "high", "medium", "low"}
    assert sum(by_category.values()) == 6 and len(by_category) >= 3


def test_total_scenarios_grows_live_as_each_page_is_designed(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    seen = []
    fake_designer.after_page = lambda: seen.append((_job(sessions, job_id).total_scenarios, _count(sessions, job_id)))

    tasks.process_url_task(str(job_id))

    assert [counter for counter, _ in seen] == [2, 4, 6]  # visible from another connection
    assert all(counter == rows for counter, rows in seen)  # never out of sync


def test_a_page_without_scenarios_is_reported_and_the_job_still_finishes(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    fake_designer.fail_urls = {f"{URL}p1": "The model declined to design scenarios for this page."}

    tasks.process_url_task(str(job_id))

    job = _job(sessions, job_id)
    assert job.status is JobStatus.DONE
    assert job.total_scenarios == _count(sessions, job_id) == 4
    assert "1 of 3 pages" in job.error and f"{URL}p1" in job.error and "declined" in job.error


def test_job_fails_when_no_page_gets_scenarios(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    fake_designer.fail_urls = {f"{URL}p{i}": "busy" for i in range(3)}

    with pytest.raises(RuntimeError):
        tasks.process_url_task(str(job_id))

    job = _job(sessions, job_id)
    assert job.status is JobStatus.FAILED and "No scenarios could be designed" in job.error
    assert job.pages_crawled == 3  # the crawl results are kept


def test_fatal_designer_error_fails_the_job_with_a_readable_message(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    fake_designer.fatal = DesignerFatalError("The Anthropic API key is missing or invalid (ANTHROPIC_API_KEY in .env).")

    with pytest.raises(DesignerFatalError):
        tasks.process_url_task(str(job_id))

    job = _job(sessions, job_id)
    assert job.status is JobStatus.FAILED and "ANTHROPIC_API_KEY" in job.error
    assert job.pages_crawled == 3 and job.total_scenarios == 0


def test_reprocessing_rebuilds_scenarios_from_sc_001(sessions, three_pages, fake_designer):
    job_id = _new_job(sessions)
    tasks.process_url_task(str(job_id))
    tasks.process_url_task(str(job_id))  # RQ retry / re-enqueue

    with sessions() as db:
        codes = db.scalars(select(Scenario.scenario_code).where(Scenario.job_id == job_id).order_by(Scenario.id)).all()
    assert codes == [f"SC-{n:03d}" for n in range(1, 7)]
    assert _job(sessions, job_id).total_scenarios == 6


def test_the_job_description_reaches_the_designer(sessions, three_pages, fake_designer):
    critical = _new_job(sessions, description="  Tienda online. Lo crítico es el checkout.  ")
    blank = _new_job(sessions, description="   ")

    tasks.process_url_task(str(critical))
    tasks.process_url_task(str(blank))

    assert fake_designer.descriptions == ["Tienda online. Lo crítico es el checkout.", None]
    assert _job(sessions, blank).description is None
