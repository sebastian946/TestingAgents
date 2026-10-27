"""Liveness and readiness (WTA-38).

- `GET /Health`: the process answers. Docker's healthcheck uses it: cheap, no dependencies,
  so a slow database never makes Docker restart a healthy API.
- `GET /Health/ready`: the whole product works. The database answers and is migrated to
  the latest revision, Redis answers, and at least one worker listens on the queue (without
  one, jobs stay "queued" forever while every other check is green). The deploy script and
  the uptime workflow poll it; 503 when any check fails.

The response names each check and its state only ("ok", "down", "behind", "none"), never an
error message, host name or version: the endpoint is public.
"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from db.conn import engine
from db.redis.redis_conn import job_queue, redis_client

router = APIRouter(tags=["health"])

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


@router.get("/Health", summary="Liveness: the API process answers")
def health():
    return {"message": "Health check ok", "status": "healthy"}


def check_database() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            try:
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                return "ok"  # no alembic table (create_all databases, tests): nothing to compare
    except Exception:
        return "down"
    head = _migration_head()
    if head is not None and current != head:
        return "behind"
    return "ok"


def _migration_head() -> str | None:
    if not ALEMBIC_INI.exists():
        return None
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(ALEMBIC_INI))
        config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:
        return None


def check_redis() -> str:
    try:
        return "ok" if redis_client.ping() else "down"
    except Exception:
        return "down"


def check_worker() -> str:
    try:
        from rq import Worker

        return "ok" if Worker.count(connection=redis_client, queue=job_queue) > 0 else "none"
    except Exception:
        return "down"


@router.get("/Health/ready", summary="Readiness: database, migrations, Redis and a worker are all fine")
def ready():
    checks = {"database": check_database(), "redis": check_redis()}
    checks["worker"] = check_worker() if checks["redis"] == "ok" else "down"
    ok = all(state == "ok" for state in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "degraded", "checks": checks})
