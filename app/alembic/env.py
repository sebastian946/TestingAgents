"""Alembic environment (WTA-4): migrations run against the database the app settings point
to, and autogenerate compares against the app's models.

Usage, from app/:
    uv run alembic upgrade head              # apply every pending migration
    uv run alembic revision --autogenerate -m "add x"   # draft a migration from model changes (review it!)
    uv run alembic stamp 0002_users           # mark an existing DB as being at that revision
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config.variables import settings
from db.conn import DATABASE_URL, Base
import db.models_db.models_db  # noqa: F401  (registers the models in Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL comes from .env through Settings, never from alembic.ini (no secrets in git).
# escape % for configparser interpolation (passwords may contain it)
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (alembic upgrade head --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        echo=settings.sql_echo,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
