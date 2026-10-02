"""Start an RQ worker that listens to the job queue (WTA-7).

Usage, from the app/ folder:  uv run python -m worker.worker

The worker runs in its own process: it blocks waiting on Redis, takes one job at a time
and executes the task it names (worker.tasks.process_url_task). It shares state with the
API only through Postgres.
"""
from rq import Worker

from db.redis.redis_conn import job_queue, redis_client


def main() -> None:
    worker = Worker([job_queue], connection=redis_client)
    worker.work()


if __name__ == "__main__":
    main()
