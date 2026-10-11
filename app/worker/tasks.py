"""Tasks executed by the RQ worker, in a process separate from the API.

Only the job id travels through Redis; each task opens its own DB session
(a SQLAlchemy session cannot be serialized, and the API's one is closed after the request).
The work itself is the LangGraph pipeline in `agents.graph` (WTA-21).
"""
import uuid

from agents import graph
from db import db_crud
from db.conn import local_session


def process_url_task(job_id: str) -> None:
    """Process a queued job: QUEUED -> RUNNING -> graph -> DONE (or FAILED)."""
    job_uuid = uuid.UUID(job_id)
    # Short session: never hold a connection open while the graph crawls and calls the LLM
    with local_session() as db:
        job = db_crud.mark_job_running(db, job_uuid)
        if job is None:
            print(f"Job {job_id} not found, skipping.")
            return
        initial_state = {"job_id": job_id, "url": job.url, "description": job.description}
    print(f"Processing URL for job {job_id}: {initial_state['url']}")

    try:
        final_state = graph.testing_graph.invoke(initial_state)
    except Exception as e:
        # Real errors (browser crash, invalid API key, database down). "Nothing to work
        # with" is not an exception: the graph routes it to its `fail` node.
        with local_session() as db:
            db_crud.mark_job_failed(db, job_uuid, str(e))
        print(f"Error occurred while processing job {job_id}: {e}")
        raise  # let RQ also register the job as failed

    with local_session() as db:
        usage = db_crud.get_job_usage(db, job_uuid)
    print(f"Job {job_id} {final_state['status']}: {len(final_state.get('pages', []))} pages, "
          f"{final_state.get('scenarios_designed', 0)} scenarios, {usage['calls']} LLM calls, "
          f"~US${float(usage['cost_usd']):.4f}." + (f" ({final_state['error']})" if final_state.get("error") else ""))
