"""Crea las tablas directamente desde los modelos (atajo para desarrollo).

Uso, desde la carpeta app/:  uv run python -m db.init_db

Para produccion, la tarea WTA-4 del tablero pide Alembic (migraciones versionadas).
"""
from db.conn import Base, engine
import db.models_db.models_db  # noqa: F401  (registra los modelos en Base.metadata)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    print("Tablas creadas:", ", ".join(Base.metadata.tables))


if __name__ == "__main__":
    init_db()
