import uuid

from redis import Redis
from rq import Queue

from config.variables import settings

redis_client = Redis(host=settings.redis_host, port=settings.redis_port, db=0, password=settings.redis_password)
job_queue = Queue(settings.rq_queue_name, connection=redis_client)


def add_new_job_to_queue(job_id: uuid.UUID) -> None:
    """Add a new job to the Redis queue."""
    # Task referenced by its import path: the API does not need to import the worker code
    # (and this avoids the db_crud -> redis_conn -> tasks -> db_crud circular import)
    job_queue.enqueue("worker.tasks.process_url_task", str(job_id))
    print(f"Added job {job_id} to the queue.")
