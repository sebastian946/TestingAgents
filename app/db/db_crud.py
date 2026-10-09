import uuid
from datetime import datetime, timezone

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from db.models_db.models_db import Job, JobStatus, LlmCall, Page, Scenario


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- Jobs

def create_job(db: Session, url: str, description: str | None = None) -> Job:
    """POST /jobs: create the job in QUEUED state.

    `description` is the user's own description of the app; the Designer uses it to weight
    priorities (WTA-15). Blank strings are stored as NULL.
    """
    job = Job(url=url, description=(description or "").strip() or None, status=JobStatus.QUEUED)
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
    """Worker: QUEUED -> RUNNING, sets started_at.

    Also wipes the results of any previous attempt (RQ retry, or a worker that died and the
    job was re-enqueued): the crawl restarts from the home page, so keeping the old rows
    would duplicate pages. Reset and status change share one commit, so nobody ever sees a
    RUNNING job that still carries the old counters.
    """
    job = db.get(Job, job_id)
    if job is None:
        return None
    db.execute(delete(Scenario).where(Scenario.job_id == job_id))  # FK to pages: delete first
    # The previous attempt's LLM calls were paid for: keep them in the job's cost, unlinked
    # from the pages about to be deleted (WTA-18)
    db.execute(update(LlmCall).where(LlmCall.job_id == job_id).values(page_id=None))
    db.execute(delete(Page).where(Page.job_id == job_id))
    job.pages_crawled = 0
    job.total_scenarios = 0
    job.report_path = None
    job.finished_at = None
    job.status = JobStatus.RUNNING
    job.started_at = _now()
    job.error = None
    db.commit()
    db.refresh(job)
    return job


def mark_job_done(
    db: Session, job_id: uuid.UUID, report_path: str | None = None, error: str | None = None
) -> Job | None:
    """Worker: RUNNING -> DONE, sets finished_at and the report path.

    `error` is a non-fatal summary for a job that finished with gaps, e.g. pages the Designer
    could not cover (WTA-17). A DONE job with an error still has usable results.
    """
    job = db.get(Job, job_id)
    if job is None:
        return None
    job.status = JobStatus.DONE
    job.finished_at = _now()
    job.error = error
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
    """Designer: insert a page's validated scenarios and update job.total_scenarios (WTA-17).

    Each dict: {"title": str, "steps": list[str], "expected_result": str, "priority": str,
    "category": str} (`TestScenario.model_dump()`). Codes SC-001, SC-002... continue across
    pages and are unique within the job (also enforced by a unique constraint). The rows and
    the counter increment share one commit, so total_scenarios always equals the row count.
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


# ------------------------------------------------------------------ LLM usage (WTA-18)

def create_llm_call(
    db: Session,
    job_id: uuid.UUID,
    *,
    page_id: int | None,
    agent: str,
    model: str,
    attempt: int,
    outcome: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cost_usd: float | None = None,
) -> LlmCall:
    """Log one billed LLM call. Committed on its own so the job's cost is visible live."""
    call = LlmCall(
        job_id=job_id,
        page_id=page_id,
        agent=agent,
        model=model,
        attempt=attempt,
        outcome=outcome,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cost_usd=cost_usd,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _usage_columns():
    return (
        func.count(LlmCall.id).label("calls"),
        func.coalesce(func.sum(LlmCall.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(LlmCall.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(LlmCall.cache_read_input_tokens), 0).label("cache_read_input_tokens"),
        func.coalesce(func.sum(LlmCall.cache_creation_input_tokens), 0).label("cache_creation_input_tokens"),
        func.coalesce(func.sum(LlmCall.cost_usd), 0).label("cost_usd"),
        func.coalesce(func.sum(case((LlmCall.cost_usd.is_(None), 1), else_=0)), 0).label("calls_without_price"),
    )


def get_job_usage(db: Session, job_id: uuid.UUID) -> dict:
    """Tokens and USD a job has spent so far, in total and per model."""
    total = db.execute(select(*_usage_columns()).where(LlmCall.job_id == job_id)).one()
    by_model = db.execute(
        select(LlmCall.model, *_usage_columns())
        .where(LlmCall.job_id == job_id)
        .group_by(LlmCall.model)
        .order_by(LlmCall.model)
    ).all()
    return {
        "job_id": job_id,
        **total._asdict(),
        "by_model": [row._asdict() for row in by_model],
    }
