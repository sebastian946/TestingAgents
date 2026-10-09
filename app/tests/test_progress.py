"""Acceptance tests for WTA-14: pages are persisted one by one and progress is visible live.

Runs `process_url_task` for real against a SQLite file (two connections, like the worker and
the API in production). The crawl itself is replaced by a fake that emits pages through the
same `on_page` callback, so no browser or network is needed.
"""
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agents.explorer import CrawledPage
from db import db_crud
from db.conn import Base
from db.models_db.models_db import Job, JobStatus, Page
from worker import tasks


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch, fake_designer):
    """A file-backed DB shared by a 'worker' session factory and an 'api' observer.

    The Designer is faked (conftest.py) so the worker never calls the Anthropic API.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(tasks, "local_session", factory)
    monkeypatch.setattr(tasks.settings, "reports_dir", str(tmp_path / "reports"))
    yield factory
    engine.dispose()


def _new_job(factory) -> uuid.UUID:
    with factory() as db:
        return db_crud.create_job(db, url="https://site.example/").id


def _observe(factory, job_id: uuid.UUID) -> tuple[int, int, JobStatus]:
    """What GET /jobs/{id} would see right now, from a separate connection."""
    with factory() as db:
        job = db.get(Job, job_id)
        rows = db.scalar(select(func.count()).select_from(Page).where(Page.job_id == job_id))
        return job.pages_crawled, rows, job.status


def _fake_crawl(n_pages: int, observer, fail_after: int | None = None):
    """Stand-in for agents.explorer.crawl: emits n pages through on_page."""
    def crawl(url, on_page=None, **_kwargs):
        pages = []
        for i in range(n_pages):
            if fail_after is not None and i == fail_after:
                raise RuntimeError("worker died mid-crawl")
            page = CrawledPage(url=f"{url}p{i}", title=f"P{i}", status_code=200, depth=1)
            on_page(page)
            observer()  # the "API" polls right after each page
            pages.append(page)
        return pages
    return crawl


def test_pages_crawled_grows_live_and_matches_rows(sessions, monkeypatch):
    job_id = _new_job(sessions)
    snapshots = []
    monkeypatch.setattr(tasks, "crawl", _fake_crawl(5, lambda: snapshots.append(_observe(sessions, job_id))))

    tasks.process_url_task(str(job_id))

    # Seen from another connection WHILE the job ran: one more page after each callback
    assert [s[0] for s in snapshots] == [1, 2, 3, 4, 5]
    assert all(counter == rows for counter, rows, _ in snapshots)  # never out of sync
    assert all(status is JobStatus.RUNNING for *_, status in snapshots)
    # At the end: counter == rows, job done
    assert _observe(sessions, job_id) == (5, 5, JobStatus.DONE)


def test_worker_dying_mid_crawl_keeps_committed_pages_consistent(sessions, monkeypatch):
    job_id = _new_job(sessions)
    monkeypatch.setattr(tasks, "crawl", _fake_crawl(5, lambda: None, fail_after=3))

    with pytest.raises(RuntimeError):
        tasks.process_url_task(str(job_id))

    # The 3 pages committed before the crash survive, and the counter agrees with them
    assert _observe(sessions, job_id) == (3, 3, JobStatus.FAILED)


def test_reprocessing_a_job_starts_clean_without_duplicates(sessions, monkeypatch):
    job_id = _new_job(sessions)
    monkeypatch.setattr(tasks, "crawl", _fake_crawl(5, lambda: None, fail_after=3))
    with pytest.raises(RuntimeError):
        tasks.process_url_task(str(job_id))
    assert _observe(sessions, job_id)[:2] == (3, 3)

    # Retry (RQ retry or re-enqueue): previous rows are wiped, not appended to
    monkeypatch.setattr(tasks, "crawl", _fake_crawl(4, lambda: None))
    tasks.process_url_task(str(job_id))
    assert _observe(sessions, job_id) == (4, 4, JobStatus.DONE)


def test_mark_job_running_resets_counters_and_error(sessions):
    job_id = _new_job(sessions)
    with sessions() as db:
        db_crud.create_page(db, job_id, url="https://site.example/old")
        db_crud.mark_job_failed(db, job_id, "boom")
        job = db_crud.mark_job_running(db, job_id)
        assert (job.pages_crawled, job.error, job.finished_at, job.status) == (0, None, None, JobStatus.RUNNING)
    assert _observe(sessions, job_id)[:2] == (0, 0)
