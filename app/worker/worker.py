"""Start an RQ worker that listens to the job queue (WTA-7).

Usage, from the app/ folder:  uv run python -m worker.worker

The worker runs in its own process: it blocks waiting on Redis, takes one job at a time
and executes the task it names (worker.tasks.process_url_task). It shares state with the
API only through Postgres.
"""
import sys

from redis.exceptions import RedisError
from rq import Worker

from db.redis.redis_conn import job_queue, redis_client


def main() -> int:
    try:
        redis_client.ping()  # fail fast with a clear message instead of a long traceback
    except RedisError as exc:
        print(f"Cannot connect to Redis at {redis_client.connection_pool.connection_kwargs.get('host')}:"
              f"{redis_client.connection_pool.connection_kwargs.get('port')}: {exc}", file=sys.stderr)
        print("Is Docker running? Start it with: docker compose --env-file .env -f app/docker-compose.yml up -d",
              file=sys.stderr)
        return 1

    # Errors INSIDE a task are handled in worker/tasks.py (the job is marked `failed`) and
    # RQ keeps the worker alive for the next job. Ctrl+C is handled by RQ too: the first one
    # finishes the current job before exiting, the second one kills it (see WTA-8).
    worker = Worker([job_queue], connection=redis_client)
    try:
        worker.work()
    except RedisError as exc:
        # Redis went away while we were waiting for jobs: nothing to recover here, exit so a
        # supervisor (or you) can restart the worker once Redis is back
        print(f"Lost connection to Redis: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
