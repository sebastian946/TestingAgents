import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from db.models_db.models_db import EmailToken, Job, JobStatus, LlmCall, Page, Scenario, Subscription, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------- Users (WTA-26)

def create_user(db: Session, email: str, password_hash: str) -> User:
    """Create an account. The caller hashes the password (security/auth.py) and checks the
    email is free first (`get_user_by_email`); the unique constraint is the last defence."""
    user = User(email=email.strip().lower(), password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(User.email == email.strip().lower())).first()


def mark_email_verified(db: Session, user: User) -> User:
    if user.email_verified_at is None:
        user.email_verified_at = _now()
        db.commit()
        db.refresh(user)
    return user


def set_password(db: Session, user: User, password_hash: str) -> User:
    """New password, and every token issued so far stops working (token_version is in the JWT)."""
    user.password_hash = password_hash
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.refresh(user)
    return user


# ----------------------------------------------------- Email tokens (WTA-28)

def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_email_token(db: Session, user_id: uuid.UUID, purpose: str, ttl: timedelta) -> str:
    """Issue a one-time token for `purpose` (verify | reset) and return the raw value to put
    in the link. Earlier unused tokens of the same purpose are spent, so only the latest
    email works. Only the hash is stored."""
    now = _now()
    db.execute(
        update(EmailToken)
        .where(EmailToken.user_id == user_id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw = secrets.token_urlsafe(32)
    db.add(EmailToken(user_id=user_id, purpose=purpose, token_hash=_token_hash(raw), expires_at=now + ttl))
    db.commit()
    return raw


def consume_email_token(db: Session, raw: str, purpose: str) -> User | None:
    """Spend a token and return its user, or None if it is unknown, used, expired or for
    another purpose. The commit is left to the caller, so the token and its effect
    (verified email, new password) land together."""
    token = db.scalars(select(EmailToken).where(EmailToken.token_hash == _token_hash(raw))).first()
    if token is None or token.purpose != purpose or token.used_at is not None:
        return None
    expires_at = token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=timezone.utc)
    if expires_at < _now():
        return None
    token.used_at = _now()
    return db.get(User, token.user_id)


def delete_user(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Delete an account with its jobs (ORM cascade: pages, scenarios, llm_calls), tokens
    and subscription. Returns the deleted job ids so the caller can remove their files."""
    user = db.get(User, user_id)
    if user is None:
        return []
    job_ids = list(db.scalars(select(Job.id).where(Job.user_id == user_id)))
    for job in db.scalars(select(Job).where(Job.user_id == user_id)):
        db.delete(job)
    db.delete(user)
    db.commit()
    return job_ids


def list_finished_jobs_before(db: Session, cutoff: datetime) -> list[uuid.UUID]:
    """Ids of done/failed jobs that finished before `cutoff` (the retention sweep)."""
    stmt = select(Job.id).where(Job.status.in_([JobStatus.DONE, JobStatus.FAILED]), Job.finished_at < cutoff)
    return list(db.scalars(stmt))


# ------------------------------------------------------- Subscriptions (WTA-30)

def get_subscription(db: Session, user_id: uuid.UUID) -> Subscription | None:
    return db.scalars(select(Subscription).where(Subscription.user_id == user_id)).first()


def upsert_subscription(db: Session, user_id: uuid.UUID, **fields) -> Subscription:
    """One subscription per user: the webhook creates it or updates it in place."""
    subscription = get_subscription(db, user_id)
    if subscription is None:
        subscription = Subscription(user_id=user_id, **fields)
        db.add(subscription)
    else:
        for key, value in fields.items():
            setattr(subscription, key, value)
    db.commit()
    db.refresh(subscription)
    return subscription


def count_jobs_since(db: Session, user_id: uuid.UUID, since: datetime) -> int:
    """Jobs a user created at or after `since` (the per-hour limit in POST /jobs)."""
    stmt = select(func.count()).select_from(Job).where(Job.user_id == user_id, Job.created_at >= since)
    return db.scalar(stmt) or 0


# ---------------------------------------------------------------- Jobs

def create_job(
    db: Session, url: str, description: str | None = None, language: str = "en", user_id: uuid.UUID | None = None,
    login_url: str | None = None, login_credentials: str | None = None,
) -> Job:
    """POST /jobs: create the job in QUEUED state.

    `description` is the user's own description of the app; the Designer uses it to weight
    priorities (WTA-15). Blank strings are stored as NULL. `user_id` is the owner: the API
    always sets it, only internal tests create ownerless jobs. `login_credentials` is the
    already-encrypted blob from security/credentials.py (WTA-29), never plain text.
    """
    job = Job(
        url=url, description=(description or "").strip() or None, language=language, status=JobStatus.QUEUED,
        user_id=user_id, login_url=login_url, login_credentials=login_credentials,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: uuid.UUID) -> Job | None:
    """GET /jobs/{id}, without ownership check (the worker and internal code use it)."""
    return db.get(Job, job_id)


def get_user_job(db: Session, user_id: uuid.UUID, job_id: uuid.UUID) -> Job | None:
    """A job only if it belongs to `user_id`; another user's job looks like it does not exist."""
    return db.scalars(select(Job).where(Job.id == job_id, Job.user_id == user_id)).first()


def get_job_with_details(db: Session, job_id: uuid.UUID) -> Job | None:
    """Job with its pages and scenarios loaded in a single query."""
    stmt = (
        select(Job)
        .where(Job.id == job_id)
        .options(selectinload(Job.pages), selectinload(Job.scenarios))
    )
    return db.scalars(stmt).first()


def list_jobs(db: Session, limit: int = 50, offset: int = 0, user_id: uuid.UUID | None = None) -> list[Job]:
    """Newest first. With `user_id`, only that user's jobs (what the API always does)."""
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    if user_id is not None:
        stmt = stmt.where(Job.user_id == user_id)
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
    job.login_credentials = None  # safety net: the explorer already cleared them (WTA-29)
    if report_path is not None:
        job.report_path = report_path
    db.commit()
    db.refresh(job)
    return job


def set_report_path(db: Session, job_id: uuid.UUID, report_path: str) -> Job | None:
    """Record where a job's report file is, without touching its status or timestamps."""
    job = db.get(Job, job_id)
    if job is None:
        return None
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
    job.login_credentials = None  # a failed job must not keep the secret either (WTA-29)
    db.commit()
    db.refresh(job)
    return job


def clear_job_credentials(db: Session, job_id: uuid.UUID) -> None:
    """Forget a job's login credentials (WTA-29): right after the crawl, whatever its outcome."""
    db.execute(update(Job).where(Job.id == job_id).values(login_credentials=None))
    db.commit()


def clear_stale_credentials(db: Session, max_age: timedelta = timedelta(hours=1)) -> int:
    """Worker start-up sweep (WTA-29): credentials of jobs that are finished, or older than
    `max_age` (a worker that died mid-crawl never reached the normal clearing). Returns how
    many rows were cleared."""
    cutoff = _now() - max_age
    result = db.execute(
        update(Job)
        .where(Job.login_credentials.is_not(None))
        .where((Job.status.in_([JobStatus.DONE, JobStatus.FAILED])) | (Job.created_at < cutoff))
        .values(login_credentials=None)
    )
    db.commit()
    return result.rowcount or 0


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
