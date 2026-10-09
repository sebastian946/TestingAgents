"""Tasks executed by the RQ worker, in a process separate from the API.

Only the job id travels through Redis; each task opens its own DB session
(a SQLAlchemy session cannot be serialized, and the API's one is closed after the request).
"""
import shutil
import uuid
from pathlib import Path

from agents.designer import PageDesign, design_pages
from agents.explorer import CrawledPage, crawl
from config.variables import settings
from db import db_crud
from db.conn import local_session


def process_url_task(job_id: str) -> None:
    """Process a queued job: QUEUED -> RUNNING -> crawl -> design -> DONE (or FAILED)."""
    job_uuid = uuid.UUID(job_id)
    db = local_session()
    try:
        job = db_crud.mark_job_running(db, job_uuid)
        if job is None:
            print(f"Job {job_id} not found, skipping.")
            return
        start_url = job.url
        description = job.description
        print(f"Processing URL for job {job_id}: {start_url}")
        # Never hold a connection (and its open transaction) during the crawl: it is minutes
        # of network and browser I/O, and an "idle in transaction" connection blocks
        # migrations and vacuum. The session is reusable after close().
        db.close()

        page_ids: dict[str, int] = {}  # crawled url -> pages.id, to link the scenarios (WTA-17)

        def persist_page(page: CrawledPage) -> None:
            # One short transaction per page (WTA-14): the row and the pages_crawled increment
            # are committed together, so GET /jobs/{id} (another process) sees the counter grow
            # live and it always equals the number of rows. If the worker dies mid-crawl, the
            # pages already committed stay; a single commit at the end would lose all of them.
            with local_session() as page_db:
                row = db_crud.create_page(
                    page_db,
                    job_uuid,
                    url=page.url,
                    title=page.title,
                    elements=page.elements,
                    page_type=page.page_type,
                    screenshot_path=page.screenshot_path,
                )
                page_ids[page.url] = row.id

        # Per-job folder: reports/<job_id>/screenshots/<page>.png (WTA-12)
        screenshot_dir = Path(settings.reports_dir) / job_id / "screenshots"
        # mark_job_running wiped the previous attempt's rows; drop its files too
        shutil.rmtree(screenshot_dir, ignore_errors=True)
        # use_browser: render JavaScript and extract the PageInfo inventory (WTA-11)
        pages = crawl(start_url, on_page=persist_page, use_browser=True, screenshot_dir=screenshot_dir)
        if not pages:
            raise RuntimeError("No page could be fetched from the given URL.")

        def persist_scenarios(outcome: PageDesign) -> None:
            # Same pattern as the pages (WTA-17): one short transaction per page, rows and
            # total_scenarios increment together, so progress is visible live and a crash
            # keeps every page already designed.
            if outcome.result is None:
                return
            with local_session() as page_db:
                db_crud.create_scenarios(
                    page_db,
                    job_uuid,
                    page_ids[outcome.page.url],
                    [scenario.model_dump() for scenario in outcome.result.scenarios],
                )
            r = outcome.result
            print(f"[designer] {outcome.page.url}: {len(r.scenarios)} scenarios "
                  f"(attempts={r.attempts}, ~US${r.estimated_cost_usd:.4f}, {r.model})")

        # Designer (WTA-15/16): one LLM call per page, after the crawl so the browser is closed.
        # A page that fails is skipped; DesignerFatalError (bad key, unknown model) fails the job.
        outcomes = design_pages(pages, description, on_result=persist_scenarios)
        failed = [o for o in outcomes if o.result is None]
        if len(failed) == len(outcomes):
            raise RuntimeError(f"No scenarios could be designed for any page. First error: {failed[0].error}")
        gaps = None
        if failed:
            gaps = f"No scenarios for {len(failed)} of {len(outcomes)} pages: " + "; ".join(
                f"{o.page.url} ({o.error})" for o in failed
            )

        # The report (WTA-19) will go here
        db_crud.mark_job_done(db, job_uuid, error=gaps)
        designed = sum(len(o.result.scenarios) for o in outcomes if o.result)
        print(f"Job {job_id} done: {len(pages)} pages crawled, {designed} scenarios designed.")
    except Exception as e:
        db.rollback()  # the session may be in a failed transaction
        db_crud.mark_job_failed(db, job_uuid, str(e))
        print(f"Error occurred while processing job {job_id}: {e}")
        raise  # let RQ also register the job as failed
    finally:
        db.close()
