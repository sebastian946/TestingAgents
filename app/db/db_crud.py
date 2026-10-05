import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db.models_db.models_db import Job, JobStatus, Page, Scenario


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- Jobs

def create_job(db: Session, url: str) -> Job:
    """POST /jobs: create the job in QUEUED state."""
    job = Job(url=url, status=JobStatus.QUEUED)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job | None:
    """GET /jobs/{id}."""
    return db.get(Job, job_id)


def get_job_with_details(db: Session, job_id: uuid.UUID) -> Job | None:
    """Job with its pages and scenarios loaded in a single query."""
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.pages), selectinload(Job.scenarios))
    )
    return db.scalars(stmt).first()


def list_jobs(db: Session, limit: int = 50, offset: int = 0) -> list[Job]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def mark_job_running(db: Session, job_id: uuid.UUID) -> Job | None:
    """Worker: QUEUED -> RUNNING, sets started_at."""
    job = db.get(Job, job_id)
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = _now()
    job.error = None
    db.commit()
    db.refresh(job)
    return job


def mark_job_done(db: Session, job_id: uuid.UUID, report_path: str | None = None) -> Job | None:
    """Worker: RUNNING -> DONE, sets finished_at and the report path."""
    job = db.get(Job, job_id)
    if job is None:
        return None
    job.status = JobStatus.DONE
    job.finished_at = _now()
    if report_path is not None:
        job.report_path = report_path
    db.commit()
    db.refresh(job)
    return job


def mark_job_failed(db: Session, job_id: uuid.UUID, error: str) -> Job | None:
    """Worker: any state -> FAILED with a readable message (no stack trace)."""
    job = db.get(Job, job_id)
    if job is None:
        return None
    job.status = JobStatus.FAILED
    job.finished_at = _now()
    job.error = error[:2000]
    db.commit()
    db.refresh(job)
    return job


def delete_job(db: Session, job_id: uuid.UUID) -> bool:
    """Delete the job and, via cascade, its pages and scenarios."""
    job = db.get(Job, job_id)
    if job is None:
        return False
    db.delete(job)
    db.commit()
    return True


# --------------------------------------------------------------- Pages

def create_page(
    db: Session,
    job_id: uuid.UUID,
    url: str,
    title: str | None = None,
    page_type: str | None = None,
    screenshot_path: str | None = None,
    elements: dict | None = None,
) -> Page:
    """Explorer: insert the page and increment job.pages_crawled in the same transaction.

    One commit per page: if the worker dies mid-crawl, what was already explored stays saved
    and the counter still matches the rows.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")

    page = Page(
        job_id=job_id,
        url=url,
        title=title,
        page_type=page_type,
        screenshot_path=screenshot_path,
        elements=elements,
    )
    db.add(page)
    job.pages_crawled = Job.pages_crawled + 1  # atomic UPDATE in SQL, not read+write in Python
    db.commit()
    db.refresh(page)
    return page


def get_page(db: Session, page_id: int) -> Page | None:
    return db.get(Page, page_id)


def get_pages_by_job(db: Session, job_id: uuid.UUID) -> list[Page]:
    stmt = select(Page).where(Page.job_id == job_id).order_by(Page.id)
    return list(db.scalars(stmt))


def update_page(db: Session, page_id: int, **fields) -> Page | None:
    """Update individual fields (title, page_type, screenshot_path, elements) after the crawl."""
    page = db.get(Page, page_id)
    if page is None:
        return None
    for name, value in fields.items():
        if name in {"title", "page_type", "screenshot_path", "elements"}:
            setattr(page, name, value)
    db.commit()
    db.refresh(page)
    return page


# ----------------------------------------------------------- Scenarios

def _next_scenario_number(db: Session, job_id: uuid.UUID) -> int:
    """Next free number for SC-XXX within the job."""
    stmt = select(Scenario.scenario_code).where(Scenario.job_id == job_id)
    codes = db.scalars(stmt).all()
    last = max((int(code.split("-")[1]) for code in codes), default=0)
    return last + 1


def create_scenarios(db: Session, job_id: uuid.UUID, page_id: int, scenarios: list[dict]) -> list[Scenario]:
    """Designer: insert a page's validated scenarios and update job.total_scenarios.

    Cada dict: {"title": str, "steps": list, "expected_result": str, "priority": str?, "category": str?}
    Codes SC-001, SC-002... are unique within the job.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    if db.get(Page, page_id) is None:
        raise ValueError(f"Page {page_id} does not exist")

    start = _next_scenario_number(db, job_id)

    created: list[Scenario] = []
    for offset, data in enumerate(scenarios):
        scenario = Scenario(
            job_id=job_id,
            page_id=page_id,
            scenario_code=f"SC-{start + offset:03d}",
            title=data["title"],
            steps=data.get("steps", []),
            expected_result=data["expected_result"],
            priority=data.get("priority"),
            category=data.get("category"),
        )
        db.add(scenario)
        created.append(scenario)

    job.total_scenarios = Job.total_scenarios + len(created)
    db.commit()
    for scenario in created:
        db.refresh(scenario)
    return created


def get_scenarios_by_job(db: Session, job_id: uuid.UUID) -> list[Scenario]:
    stmt = select(Scenario).where(Scenario.job_id == job_id).order_by(Scenario.id)
    return list(db.scalars(stmt))


def get_scenarios_by_page(db: Session, page_id: int) -> list[Scenario]:
    stmt = select(Scenario).where(Scenario.page_id == page_id).order_by(Scenario.id)
    return list(db.scalars(stmt))
