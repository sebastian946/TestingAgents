"""Create the tables directly from the models (development shortcut).

Usage, from the app/ folder:  uv run python -m db.init_db

For production, board task WTA-4 calls for Alembic (versioned migrations).
"""
from db.conn import Base, engine
import db.models_db.models_db  # noqa: F401  (registers the models in Base.metadata)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    print("Tables created:", ", ".join(Base.metadata.tables))


if __name__ == "__main__":
    init_db()
