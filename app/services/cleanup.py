"""Deleting what a job left on disk, and the retention sweep (WTA-30).

Rows go through the ORM cascade (job -> pages, scenarios, llm_calls); the files live in
`reports/`: the Excel report and the per-job screenshots folder. Both are removed together
here, whether the job is deleted by its owner, with its owner's account, or by the
retention policy (`RETENTION_DAYS`, run by the worker).
"""
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from agents.documenter import report_file
from db import db_crud


def delete_job_files(reports_dir: str | Path, job_id: uuid.UUID | str) -> None:
    report_file(reports_dir, job_id).unlink(missing_ok=True)
    shutil.rmtree(Path(reports_dir) / str(job_id), ignore_errors=True)


def delete_job_completely(db: Session, reports_dir: str | Path, job_id: uuid.UUID) -> bool:
    deleted = db_crud.delete_job(db, job_id)
    if deleted:
        delete_job_files(reports_dir, job_id)
    return deleted


def delete_account(db: Session, reports_dir: str | Path, user_id: uuid.UUID) -> int:
    """Delete a user with everything they own; returns the number of jobs removed."""
    job_ids = db_crud.delete_user(db, user_id)
    for job_id in job_ids:
        delete_job_files(reports_dir, job_id)
    return len(job_ids)


def purge_expired_jobs(db: Session, reports_dir: str | Path, retention_days: int) -> int:
    """Delete finished jobs older than `retention_days` (0 = keep everything)."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    job_ids = db_crud.list_finished_jobs_before(db, cutoff)
    for job_id in job_ids:
        delete_job_completely(db, reports_dir, job_id)
    return len(job_ids)
