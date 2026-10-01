import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from db import db_crud
from db.conn import get_db
from models.models import JobCreate, JobRead, PageRead, ScenarioRead
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
    return db_crud.create_job(db, url=url)


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


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a job and its pages/scenarios",
)
def delete_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    if not db_crud.delete_job(db, job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
