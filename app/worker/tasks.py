"""Tasks executed by the RQ worker, in a process separate from the API.

Only the job id travels through Redis; each task opens its own DB session
(a SQLAlchemy session cannot be serialized, and the API's one is closed after the request).
"""
import time
import uuid

from db import db_crud
from db.conn import local_session


def process_url_task(job_id: str) -> None:
    """Process a queued job: QUEUED -> RUNNING -> DONE (or FAILED)."""
    job_uuid = uuid.UUID(job_id)
    db = local_session()
    try:
        job = db_crud.mark_job_running(db, job_uuid)
        if job is None:
            print(f"Job {job_id} not found, skipping.")
            return
        print(f"Processing URL for job {job_id}: {job.url}")
        time.sleep(5)  # Simulate processing time (the Explorer will go here)
        db_crud.mark_job_done(db, job_uuid, report_path=f"/reports/{job_id}.json")
        print(f"Processed URL for job {job_id}: {job.url}")
    except Exception as e:
        db.rollback()  # the session may be in a failed transaction
        db_crud.mark_job_failed(db, job_uuid, str(e))
        print(f"Error occurred while processing URL for job {job_id}: {e}")
        raise  # let RQ also register the job as failed
    finally:
        db.close()
