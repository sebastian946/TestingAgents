"""Automated test runs (WTA-33, ADR-10): start one, follow it, read the verdicts."""
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from config.variables import settings
from db import db_crud
from db.conn import get_db
from db.models_db.models_db import Job, JobStatus, RunStatus, User
from db.redis.redis_conn import enqueue_test_run
from models.models import RunCreate, RunRead
from routes.routes import owned_job
from agents.explorer import same_site
from security import credentials
from security.auth import get_current_user, require_verified_email
from security.ssrf import UnsafeURLError, validate_public_url
from services import billing

router = APIRouter(prefix="/jobs/{job_id}/runs", tags=["runs"], dependencies=[Depends(get_current_user)])


def _read(db: Session, run) -> RunRead:
    data = RunRead.model_validate(run)
    data.results = [RunRead.model_fields["results"].annotation.__args__[0].model_validate(r) for r in db_crud.get_results_by_run(db, run.id)]
    return data


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunRead,
    summary="Run the job's scenarios in a browser (Pro plans)",
    responses={402: {"description": "The plan has no test runs left this month"},
               409: {"description": "The job has no scenarios yet, or a run is already in progress"}},
)
def start_run(payload: RunCreate, job: Job = Depends(owned_job), user: User = Depends(require_verified_email),
              db: Session = Depends(get_db)):
    """The Executor acts on the site (fills forms, clicks), so it only runs on a test
    environment the user confirms. Counts toward the plan's `test_runs_per_month`."""
    if not payload.test_environment:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Confirm that the site is a test environment: the run fills forms and clicks buttons.")
    usage = billing.usage_for(db, user)
    if usage.plan.test_runs_per_month == 0:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail={
            "message": "Automated test runs are included in the Pro plan.", "plan": usage.plan.code,
            "used": 0, "limit": 0})
    if usage.runs_remaining <= 0:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail={
            "message": f"Your {usage.plan.name} plan includes {usage.plan.test_runs_per_month} test runs per month and "
                       f"you have used {usage.runs_this_month}.", "plan": usage.plan.code,
            "used": usage.runs_this_month, "limit": usage.plan.test_runs_per_month})
    if job.status is not JobStatus.DONE or job.total_scenarios == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The job needs finished scenarios before they can be run.")
    if any(r.status in (RunStatus.QUEUED, RunStatus.RUNNING) for r in db_crud.get_runs_by_job(db, job.id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A run of this job is already in progress.")

    encrypted = None
    if payload.login is not None:
        if not payload.login.authorized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Confirm that you are authorized to test this site with this account.")
        if not credentials.configured():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="This server is not configured to store login credentials (CREDENTIALS_KEY).")
        try:
            login_url = validate_public_url(payload.login.login_url or job.url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Login URL: {exc}")
        if not same_site(login_url, job.url):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The login page must be on the same site as the job.")
        encrypted = credentials.encrypt(credentials.LoginSpec(login_url, payload.login.username, payload.login.password))

    run = db_crud.create_run(db, job.id, login_credentials=encrypted)
    try:
        enqueue_test_run(run.id)
    except RedisError:
        db_crud.finish_run(db, run.id, error="Could not enqueue the run.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The job queue is unavailable, try again later.")
    return _read(db, run)


@router.get("", response_model=list[RunRead], summary="The job's runs, newest first (without results)")
def list_runs(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return [RunRead.model_validate(r) for r in db_crud.get_runs_by_job(db, job.id)]


def _owned_run(run_id: int, job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    run = db_crud.get_run(db, run_id)
    if run is None or run.job_id != job.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return run


@router.get("/{run_id}", response_model=RunRead, summary="A run with its verdicts")
def get_run(run=Depends(_owned_run), db: Session = Depends(get_db)):
    return _read(db, run)


@router.get("/{run_id}/results/{result_id}/screenshot", response_class=FileResponse, summary="Final-state screenshot of a verdict")
def result_screenshot(result_id: int, run=Depends(_owned_run), db: Session = Depends(get_db)):
    result = next((r for r in db_crud.get_results_by_run(db, run.id) if r.id == result_id), None)
    if result is None or not result.screenshot_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No screenshot for this result")
    # The stored path is always under reports/<job>/runs/<run>/ (worker); never trust it blindly
    path = Path(result.screenshot_path).resolve()
    root = Path(settings.reports_dir).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No screenshot for this result")
    return FileResponse(path, media_type="image/png")


def _now() -> datetime:
    return datetime.now(timezone.utc)
