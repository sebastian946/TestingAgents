"""WTA-38: liveness and readiness. The checks are replaced, except the database one, which
runs against a SQLite file with and without an alembic_version table."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from main import app
from routes import health_routes


@pytest.fixture
def client():
    return TestClient(app)


def test_liveness_needs_nothing(client):
    r = client.get("/Health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


def test_ready_when_every_check_passes(client, monkeypatch):
    for name in ("check_database", "check_redis", "check_worker"):
        monkeypatch.setattr(health_routes, name, lambda: "ok")
    r = client.get("/Health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok", "worker": "ok"}}


@pytest.mark.parametrize("database, redis, worker, expected_worker", [
    ("down", "ok", "ok", "ok"),
    ("behind", "ok", "ok", "ok"),
    ("ok", "ok", "none", "none"),
    ("ok", "down", "ok", "down"),  # without Redis the worker check is not even tried
])
def test_degraded_is_503_and_names_the_check(client, monkeypatch, database, redis, worker, expected_worker):
    monkeypatch.setattr(health_routes, "check_database", lambda: database)
    monkeypatch.setattr(health_routes, "check_redis", lambda: redis)
    monkeypatch.setattr(health_routes, "check_worker", lambda: worker)
    r = client.get("/Health/ready")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert r.json()["checks"] == {"database": database, "redis": redis, "worker": expected_worker}


def test_database_check_compares_the_migration_head(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'h.db'}")
    monkeypatch.setattr(health_routes, "engine", engine)
    assert health_routes.check_database() == "ok"  # no alembic table: nothing to compare

    head = health_routes._migration_head()
    assert head is not None  # alembic.ini and the versions folder are found from the module
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0001')"))
    assert health_routes.check_database() == ("ok" if head == "0001" else "behind")
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :h"), {"h": head})
    assert health_routes.check_database() == "ok"

    monkeypatch.setattr(health_routes, "engine", create_engine(f"sqlite:///{tmp_path / 'missing' / 'x.db'}"))
    assert health_routes.check_database() == "down"  # cannot even open the file


def test_redis_and_worker_checks_report_down_instead_of_raising(monkeypatch):
    class Broken:
        def ping(self):
            raise ConnectionError("no redis")

    monkeypatch.setattr(health_routes, "redis_client", Broken())
    assert health_routes.check_redis() == "down"

    import rq

    def counted(n):
        return classmethod(lambda cls, **kwargs: n)

    monkeypatch.setattr(rq.Worker, "count", counted(1))
    assert health_routes.check_worker() == "ok"
    monkeypatch.setattr(rq.Worker, "count", counted(0))
    assert health_routes.check_worker() == "none"

    def boom(cls, **kwargs):
        raise ConnectionError("no redis")

    monkeypatch.setattr(rq.Worker, "count", classmethod(boom))
    assert health_routes.check_worker() == "down"
