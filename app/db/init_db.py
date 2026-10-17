"""Create the tables directly from the models (development shortcut), then stamp the
database as being at the latest Alembic revision, so `alembic upgrade head` later is a
no-op instead of failing on tables that already exist.

Usage, from the app/ folder:  uv run python -m db.init_db

Deployments run `alembic upgrade head` instead (WTA-4): it also applies schema changes to
existing tables, which create_all never does.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config

from db.conn import Base, engine
import db.models_db.models_db  # noqa: F401  (registers the models in Base.metadata)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    print("Tables created:", ", ".join(Base.metadata.tables))
    command.stamp(Config(str(ALEMBIC_INI)), "head")


if __name__ == "__main__":
    init_db()
