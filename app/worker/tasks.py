"""Tasks executed by the RQ worker, in a process separate from the API.

Only the job id travels through Redis; each task opens its own DB session
(a SQLAlchemy session cannot be serialized, and the API's one is closed after the request).
"""
import uuid
from pathlib import Path

from agents.explorer import CrawledPage, crawl
from config.variables import settings
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

        def persist_page(page: CrawledPage) -> None:
            # One commit per page: GET /jobs/{id} shows pages_crawled growing live (WTA-14)
            db_crud.create_page(
                db,
                job_uuid,
                url=page.url,
                title=page.title,
                elements=page.elements,
                screenshot_path=page.screenshot_path,
            )

        # Per-job folder: reports/<job_id>/screenshots/<page>.png (WTA-12)
        screenshot_dir = Path(settings.reports_dir) / job_id / "screenshots"
        # use_browser: render JavaScript and extract the PageInfo inventory (WTA-11)
        pages = crawl(job.url, on_page=persist_page, use_browser=True, screenshot_dir=screenshot_dir)
        if not pages:
            raise RuntimeError("No page could be fetched from the given URL.")

        # The Designer (WTA-15) and the report (WTA-19) will go here
        db_crud.mark_job_done(db, job_uuid)
        print(f"Job {job_id} done: {len(pages)} pages crawled.")
    except Exception as e:
        db.rollback()  # the session may be in a failed transaction
        db_crud.mark_job_failed(db, job_uuid, str(e))
        print(f"Error occurred while processing job {job_id}: {e}")
        raise  # let RQ also register the job as failed
    finally:
        db.close()
