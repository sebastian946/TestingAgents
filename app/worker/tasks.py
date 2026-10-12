"""Tasks executed by the RQ worker, in a process separate from the API.

Only the job id travels through Redis; each task opens its own DB session
(a SQLAlchemy session cannot be serialized, and the API's one is closed after the request).
The work itself is the LangGraph pipeline in `agents.graph` (WTA-21).
"""
import uuid

from agents import graph
from config.variables import TRACING_ENABLED, settings
from db import db_crud
from db.conn import local_session


def _trace_config(job_id: str, url: str, language: str = "en") -> dict:
    """LangSmith labels for this run: findable by job id, url or model in the UI."""
    return {
        "run_name": f"job {job_id}",
        "tags": [
            "job", f"model:{settings.designer_model}", f"prompt:{settings.designer_prompt_version}", f"lang:{language}",
        ],
        "metadata": {
            "job_id": job_id,
            "url": url,
            "designer_model": settings.designer_model,
            "designer_effort": settings.designer_effort,
            "prompt_version": settings.designer_prompt_version,
            "language": language,
        },
    }


def _flush_traces() -> None:
    """Send pending LangSmith traces before the task returns.

    Traces are uploaded by a background thread. On Linux the RQ worker runs each job in a
    forked child that exits right after it, which would drop the last batch.
    """
    if not TRACING_ENABLED:
        return
    try:
        from langchain_core.tracers.langchain import wait_for_all_tracers
        from langsmith.run_trees import get_cached_client

        wait_for_all_tracers()  # graph and node runs (LangGraph)
        get_cached_client().flush()  # Claude calls (wrap_anthropic)
    except Exception as exc:  # tracing must never fail a job
        print(f"Could not flush LangSmith traces: {exc}")


def process_url_task(job_id: str) -> None:
    """Process a queued job: QUEUED -> RUNNING -> graph -> DONE (or FAILED)."""
    job_uuid = uuid.UUID(job_id)
    # Short session: never hold a connection open while the graph crawls and calls the LLM
    with local_session() as db:
        job = db_crud.mark_job_running(db, job_uuid)
        if job is None:
            print(f"Job {job_id} not found, skipping.")
            return
        initial_state = {
            "job_id": job_id, "url": job.url, "description": job.description, "language": job.language,
        }
    print(f"Processing URL for job {job_id}: {initial_state['url']}")

    try:
        config = _trace_config(job_id, initial_state["url"], initial_state["language"])
        final_state = graph.testing_graph.invoke(initial_state, config=config)
    except Exception as e:
        # Real errors (browser crash, invalid API key, database down). "Nothing to work
        # with" is not an exception: the graph routes it to its `fail` node.
        with local_session() as db:
            db_crud.mark_job_failed(db, job_uuid, str(e))
        print(f"Error occurred while processing job {job_id}: {e}")
        raise  # let RQ also register the job as failed
    finally:
        _flush_traces()

    with local_session() as db:
        usage = db_crud.get_job_usage(db, job_uuid)
    print(f"Job {job_id} {final_state['status']}: {len(final_state.get('pages', []))} pages, "
          f"{final_state.get('scenarios_designed', 0)} scenarios, {usage['calls']} LLM calls, "
          f"~US${float(usage['cost_usd']):.4f}." + (f" ({final_state['error']})" if final_state.get("error") else ""))
