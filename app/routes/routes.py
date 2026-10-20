import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from agents.documenter import XLSX_MEDIA_TYPE, download_filename, generate_report, report_file
from config.variables import settings
from db import db_crud
from db.conn import get_db
from db.models_db.models_db import Job, JobStatus, User
from db.redis.redis_conn import add_new_job_to_queue, enqueue_continuation
from models.models import JobCreate, JobRead, JobUsage, PageRead, PageSelection, ScenarioRead
from security import credentials
from services import billing, cleanup
from security.auth import get_current_user, require_verified_email
from security.ssrf import UnsafeURLError, validate_public_url

# Every route needs a logged-in user (WTA-26): the dependency runs before the handler and
# answers 401 by itself. Jobs are then scoped to that user: another user's job is a 404.
router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(get_current_user)])


def owned_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Job:
    """Path dependency: the job in the URL, only if it belongs to the caller."""
    job = db_crud.get_user_job(db, user.id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return job


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRead,
    summary="Create a new job test execution",
)
def create_job(payload: JobCreate, user: User = Depends(require_verified_email), db: Session = Depends(get_db)):
    """Receive the URL, store the job as `queued` for the caller and respond 202.

    The test is NOT run here: the worker processes it; poll GET /jobs/{id}.
    Responds 403 until the account's email is verified (WTA-28), 400 if the URL is not
    http/https or points to an internal network (anti-SSRF),
    and 429 once the user has created `AUTH_MAX_JOBS_PER_HOUR` jobs in the last hour: each
    job spends real LLM money, so a leaked token or a loop cannot run up the bill.
    """
    try:
        url = validate_public_url(payload.url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    login_url = encrypted = None
    if payload.login is not None:
        # WTA-29: same anti-SSRF check on the login page, which must be on the site under test;
        # the secret is encrypted before it touches the database and never echoed back
        if not payload.login.authorized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Confirm that you are authorized to test this site with this account.")
        if not credentials.configured():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="This server is not configured to store login credentials (CREDENTIALS_KEY).")
        try:
            login_url = validate_public_url(payload.login.login_url or url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Login URL: {exc}")
        if urlsplit(login_url).hostname != urlsplit(url).hostname:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The login page must be on the same site as the URL under test.")
        encrypted = credentials.encrypt(credentials.LoginSpec(login_url, payload.login.username, payload.login.password))

    # Monthly allowance of the plan (WTA-30, ADR-09): 402 with the numbers, so the dashboard
    # can offer the upgrade. The hourly cap below stays as abuse protection
    usage = billing.usage_for(db, user)
    if usage.remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": f"Your {usage.plan.name} plan includes {usage.plan.jobs_per_month} jobs per month and "
                           f"you have used {usage.used_this_month}. Upgrade to keep testing.",
                "plan": usage.plan.code, "used": usage.used_this_month, "limit": usage.plan.jobs_per_month,
            },
        )

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    if db_crud.count_jobs_since(db, user.id, since) >= settings.auth_max_jobs_per_hour:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Limit of {settings.auth_max_jobs_per_hour} jobs per hour reached. Try again later.",
            headers={"Retry-After": "3600"},
        )

    job = db_crud.create_job(
        db, url=url, description=payload.description, language=payload.language, user_id=user.id,
        login_url=login_url, login_credentials=encrypted, select_pages=payload.select_pages,
    )
    try:
        add_new_job_to_queue(job.id)
    except RedisError:
        # Without this the job would stay `queued` forever with nobody to process it
        db_crud.mark_job_failed(db, job.id, "Could not enqueue the job.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The job queue is unavailable, try again later.",
        )
    return job


@router.get(
    "",
    response_model=list[JobRead],
    summary="List jobs",
)
def list_jobs(limit: int = 50, offset: int = 0, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db_crud.list_jobs(db, limit=limit, offset=offset, user_id=user.id)


@router.get(
    "/{job_id}",
    response_model=JobRead,
    summary="Get job detail",
)
def get_job(job: Job = Depends(owned_job)):
    return job


@router.get(
    "/{job_id}/pages",
    response_model=list[PageRead],
    summary="Pages crawled for a job",
)
def get_job_pages(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return db_crud.get_pages_by_job(db, job.id)


@router.post(
    "/{job_id}/pages/select",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Choose the pages that get scenarios and resume the job",
    responses={409: {"description": "The job is not waiting for a selection"}, 400: {"description": "Unknown page ids"}},
)
def select_job_pages(payload: PageSelection, job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    """WTA-32: only for a job in `awaiting_selection`. The chosen pages are designed, the
    rest are kept in the job but get no scenarios. Answers 202 like POST /jobs: the worker
    continues, poll GET /jobs/{id}."""
    if job.status is not JobStatus.AWAITING_SELECTION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The job is {job.status.value}, not waiting for a page selection.",
        )
    known = {p.id for p in db_crud.get_pages_by_job(db, job.id)}
    unknown = sorted(set(payload.page_ids) - known)
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown page ids: {unknown}")
    db_crud.select_pages(db, job.id, payload.page_ids)
    job.status = JobStatus.QUEUED
    db.commit()
    try:
        enqueue_continuation(job.id)
    except RedisError:
        db_crud.mark_job_awaiting_selection(db, job.id)  # back to waiting: the user can retry
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The job queue is unavailable, try again later.",
        )
    db.refresh(job)
    return job


@router.get(
    "/{job_id}/scenarios",
    response_model=list[ScenarioRead],
    summary="Scenarios generated for a job",
)
def get_job_scenarios(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return db_crud.get_scenarios_by_job(db, job.id)


@router.get(
    "/{job_id}/usage",
    response_model=JobUsage,
    summary="LLM tokens and estimated cost of a job",
)
def get_job_usage(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return db_crud.get_job_usage(db, job.id)


REPORT_NOT_READY_RETRY_SECONDS = 15


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


@router.get(
    "/{job_id}/report",
    response_class=FileResponse,
    summary="Download the Excel report of a finished job",
    responses={
        200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "The .xlsx report as a file download"},
        404: {"description": "No job with this id"},
        409: {"description": "The job exists but has no report: still running, or failed"},
    },
)
def download_report(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    """Return the job's Excel report as a download named test-report-{domain}-{date}.xlsx.

    404 only when the job does not exist. A job that exists but has no report yet answers
    **409 Conflict**: the resource is real, its current state just does not allow this
    request. A 404 there would tell a polling client the id is wrong and make it give up.
    While the job runs, the 409 carries `Retry-After` and the progress; a failed job never
    gets a report, so its 409 says why. `detail.status` lets clients branch on the case.
    """
    job_id = job.id
    if job.status is JobStatus.AWAITING_SELECTION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "status": job.status.value,
                "message": f"The job is waiting for you to choose which of its {job.pages_crawled} pages get scenarios "
                           f"(POST /jobs/{job_id}/pages/select).",
            },
        )
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "status": job.status.value,
                "message": (
                    f"The report is not ready yet: the job is {job.status.value} "
                    f"({_plural(job.pages_crawled, 'page')} explored, "
                    f"{_plural(job.total_scenarios, 'scenario')} so far). "
                    f"Poll GET /jobs/{job_id} and download the report when its status is done."
                ),
            },
            headers={"Retry-After": str(REPORT_NOT_READY_RETRY_SECONDS)},
        )
    if job.status is JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "status": job.status.value,
                "message": f"The job failed, so it has no report. Reason: {job.error or 'unknown'}",
            },
        )

    # The path is derived from the job id, never read from the database, so nothing stored
    # can point this endpoint at another file
    path = report_file(settings.reports_dir, job.id)
    if not path.is_file():
        # Done before reports existed, or the file was removed: rebuild it from the database
        # (no LLM call involved, the scenarios are already stored)
        path = generate_report(db, job.id, settings.reports_dir)
        db_crud.set_report_path(db, job.id, str(path))

    return FileResponse(
        path,
        media_type=XLSX_MEDIA_TYPE,
        filename=download_filename(job.url, job.finished_at or job.created_at, job.language),
    )


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a job and its pages/scenarios",
)
def delete_job(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    cleanup.delete_job_completely(db, settings.reports_dir, job.id)  # rows and files (WTA-30)
