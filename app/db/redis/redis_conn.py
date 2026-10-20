import uuid

from redis import Redis
from rq import Queue, Retry

from config.variables import settings

redis_client = Redis(host=settings.redis_host, port=settings.redis_port, db=0, password=settings.redis_password)
job_queue = Queue(settings.rq_queue_name, connection=redis_client)


def enqueue_options() -> dict:
    """How a job is enqueued (WTA-8): a hard timeout per attempt, so a stuck crawl cannot
    hold a worker for hours, and automatic retries for real failures (the task re-raises
    them). Results are not kept: everything lives in the database."""
    options = {"job_timeout": settings.job_timeout_seconds, "result_ttl": 0, "failure_ttl": 24 * 3600}
    if settings.job_max_retries > 0:
        options["retry"] = Retry(max=settings.job_max_retries, interval=30)
    return options


def enqueue_continuation(job_id: uuid.UUID) -> None:
    """WTA-32: resume a job after the user selected its pages (designer + documenter)."""
    job_queue.enqueue("worker.tasks.continue_job_task", str(job_id), **enqueue_options())
    print(f"Enqueued the continuation of job {job_id}.")


def add_new_job_to_queue(job_id: uuid.UUID) -> None:
    """Add a new job to the Redis queue."""
    # Task referenced by its import path: the API does not need to import the worker code
    # (and this avoids the db_crud -> redis_conn -> tasks -> db_crud circular import)
    job_queue.enqueue("worker.tasks.process_url_task", str(job_id), **enqueue_options())
    print(f"Added job {job_id} to the queue.")
