"""Tasks executed by the RQ worker, in a process separate from the API.

Only the job id travels through Redis; each task opens its own DB session
(a SQLAlchemy session cannot be serialized, and the API's one is closed after the request).
The work itself is the LangGraph pipeline in `agents.graph` (WTA-21).
"""
import uuid

from rq import get_current_job

from agents import graph
from worker.run_tests import run_tests
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


def _retries_left() -> int:
    """Attempts RQ will still make for the current job (0 outside a worker)."""
    try:
        job = get_current_job()
    except Exception:  # no Redis connection (tests)
        return 0
    return int(getattr(job, "retries_left", 0) or 0) if job is not None else 0


def _failure_reason(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    if exc.__class__.__name__ == "JobTimeoutException":
        text = f"The job exceeded the time limit of {settings.job_timeout_seconds // 60} minutes."
    left = _retries_left()
    if left > 0:
        text += f" Retrying automatically ({left} attempt{'s' if left != 1 else ''} left)."
    return text


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
            "select_pages": bool(job.select_pages),
        }
    print(f"Processing URL for job {job_id}: {initial_state['url']}")
    _run(graph.testing_graph, job_id, job_uuid, initial_state)


def continue_job_task(job_id: str) -> None:
    """WTA-32: a job that paused for the page selection; runs designer + documenter on the
    pages the user kept. Enqueued by POST /jobs/{id}/pages/select."""
    job_uuid = uuid.UUID(job_id)
    with local_session() as db:
        job = db_crud.mark_job_designing(db, job_uuid)
        if job is None:
            print(f"Job {job_id} not found, skipping.")
            return
        pages = db_crud.get_selected_pages(db, job_uuid)
        initial_state = {
            "job_id": job_id, "url": job.url, "description": job.description, "language": job.language,
            "select_pages": True,
            "pages": [graph.PageState(page_id=p.id, url=p.url, title=p.title, page_type=p.page_type, elements=p.elements)
                      for p in pages],
        }
    print(f"Continuing job {job_id} with {len(initial_state['pages'])} selected pages")
    _run(graph.continuation_graph, job_id, job_uuid, initial_state)


def run_tests_task(run_id: int) -> None:
    """WTA-33: execute a job's scenarios in the browser; enqueued by POST /jobs/{id}/runs."""
    try:
        summary = run_tests(run_id)
    except Exception as e:
        with local_session() as db:
            db_crud.finish_run(db, run_id, error=_failure_reason(e))
        print(f"Error in test run {run_id}: {e}")
        raise
    finally:
        _flush_traces()
    print(f"Test run {run_id} {summary['status']}: {summary.get('passed', 0)} passed, "
          f"{summary.get('failed', 0)} failed, {summary.get('blocked', 0)} blocked.")


def _run(compiled_graph, job_id: str, job_uuid: uuid.UUID, initial_state: dict) -> None:
    try:
        config = _trace_config(job_id, initial_state["url"], initial_state["language"])
        final_state = compiled_graph.invoke(initial_state, config=config)
    except Exception as e:
        # Real errors (browser crash, invalid API key, database down, the RQ timeout).
        # "Nothing to work with" is not an exception: the graph routes it to its `fail`
        # node. RQ re-enqueues the job while it has retries left (WTA-8); the job row says
        # so, so a client polling it knows another attempt is coming
        reason = _failure_reason(e)
        with local_session() as db:
            db_crud.mark_job_failed(db, job_uuid, reason)
        print(f"Error occurred while processing job {job_id}: {reason}")
        raise  # let RQ register the failure (and retry, if any attempts are left)
    finally:
        _flush_traces()

    with local_session() as db:
        usage = db_crud.get_job_usage(db, job_uuid)
    print(f"Job {job_id} {final_state['status']}: {len(final_state.get('pages', []))} pages, "
          f"{final_state.get('scenarios_designed', 0)} scenarios, {usage['calls']} LLM calls, "
          f"~US${float(usage['cost_usd']):.4f}." + (f" ({final_state['error']})" if final_state.get("error") else ""))
