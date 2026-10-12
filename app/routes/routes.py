import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from agents.documenter import XLSX_MEDIA_TYPE, download_filename, generate_report, report_file
from config.variables import settings
from db import db_crud
from db.conn import get_db
from db.models_db.models_db import JobStatus
from db.redis.redis_conn import add_new_job_to_queue
from models.models import JobCreate, JobRead, JobUsage, PageRead, ScenarioRead
from security.ssrf import UnsafeURLError, validate_public_url

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobRead,
    summary="Create a new job test execution",
)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    """Receive the URL, store the job as `queued` and respond 202.

    The test is NOT run here: the worker will process it (next task on the board).
    Responds 400 if the URL is not http/https or points to an internal network (anti-SSRF).
    """
    try:
        url = validate_public_url(payload.url)
    except UnsafeURLError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    job = db_crud.create_job(db, url=url, description=payload.description, language=payload.language)
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
def list_jobs(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    return db_crud.list_jobs(db, limit=limit, offset=offset)


@router.get(
    "/{job_id}",
    response_model=JobRead,
    summary="Get job detail",
)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db_crud.get_job(db, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job


@router.get(
    "/{job_id}/pages",
    response_model=list[PageRead],
    summary="Pages crawled for a job",
)
def get_job_pages(job_id: uuid.UUID, db: Session = Depends(get_db)):
    if db_crud.get_job(db, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return db_crud.get_pages_by_job(db, job_id)


@router.get(
    "/{job_id}/scenarios",
    response_model=list[ScenarioRead],
    summary="Scenarios generated for a job",
)
def get_job_scenarios(job_id: uuid.UUID, db: Session = Depends(get_db)):
    if db_crud.get_job(db, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return db_crud.get_scenarios_by_job(db, job_id)


@router.get(
    "/{job_id}/usage",
    response_model=JobUsage,
    summary="LLM tokens and estimated cost of a job",
)
def get_job_usage(job_id: uuid.UUID, db: Session = Depends(get_db)):
    if db_crud.get_job(db, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return db_crud.get_job_usage(db, job_id)


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
def download_report(job_id: uuid.UUID, db: Session = Depends(get_db)):
    """Return the job's Excel report as a download named test-report-{domain}-{date}.xlsx.

    404 only when the job does not exist. A job that exists but has no report yet answers
    **409 Conflict**: the resource is real, its current state just does not allow this
    request. A 404 there would tell a polling client the id is wrong and make it give up.
    While the job runs, the 409 carries `Retry-After` and the progress; a failed job never
    gets a report, so its 409 says why. `detail.status` lets clients branch on the case.
    """
    job = db_crud.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")

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
def delete_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    if not db_crud.delete_job(db, job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
