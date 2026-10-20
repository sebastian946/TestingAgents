"""Start an RQ worker that listens to the job queue (WTA-7).

Usage, from the app/ folder:  uv run python -m worker.worker

The worker runs in its own process: it blocks waiting on Redis, takes one job at a time
and executes the task it names (worker.tasks.process_url_task). It shares state with the
API only through Postgres.
"""
import os
import sys
import threading

# Logs carry LLM output and page text (e.g. "→"). The Windows console defaults to cp1252,
# which cannot encode them and makes logging print "--- Logging error ---" tracebacks
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

from redis.exceptions import RedisError  # noqa: E402
from rq import SimpleWorker, Worker  # noqa: E402

from config.variables import settings  # noqa: E402
from observability import init_sentry  # noqa: E402
from services.cleanup import purge_expired_jobs  # noqa: E402
from db import db_crud  # noqa: E402
from db.conn import local_session  # noqa: E402
from db.redis.redis_conn import job_queue, redis_client  # noqa: E402


RETENTION_INTERVAL_SECONDS = 6 * 3600


def _retention_loop() -> None:
    import time

    while True:
        try:
            with local_session() as db:
                purged = purge_expired_jobs(db, settings.reports_dir, settings.retention_days)
            if purged:
                print(f"Retention: deleted {purged} job(s) older than {settings.retention_days} days.", flush=True)
        except Exception as exc:  # noqa: BLE001  never take the worker down for housekeeping
            print(f"Retention sweep failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(RETENTION_INTERVAL_SECONDS)


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
    # The default Worker runs every job in a forked child (a crash or leak in one job cannot
    # take the worker down). os.fork does not exist on Windows, so there the job runs in the
    # worker process itself; inside Docker (Linux) the forking Worker is used.
    if settings.designer_mode == "fake":
        print("WARNING: DESIGNER_MODE=fake. Scenarios come from fixed rules, not from Claude. "
              "Use it only for tests.", file=sys.stderr)
    # WTA-29: a worker that died mid-crawl never cleared that job's login credentials
    with local_session() as db:
        cleared = db_crud.clear_stale_credentials(db)
    if cleared:
        print(f"Cleared login credentials of {cleared} finished or stale job(s).")
    if init_sentry("worker"):
        print("Sentry error reporting on.")
    # WTA-30: data retention. Finished jobs older than RETENTION_DAYS are deleted with their
    # files, now and then every 6 hours, from a thread that only touches the database
    threading.Thread(target=_retention_loop, daemon=True, name="retention").start()
    print(f"Listening on RQ queue {settings.rq_queue_name!r}")
    worker_class = Worker if hasattr(os, "fork") else SimpleWorker
    worker = worker_class([job_queue], connection=redis_client)
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
