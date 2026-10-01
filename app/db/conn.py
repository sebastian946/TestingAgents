from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config.variables import settings

DATABASE_URL = (
    f"postgresql+psycopg://{settings.db_user}:{settings.db_password}"
    f"@{settings.endpoint}:{settings.db_port}/{settings.db_name}"
)

engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True)

local_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: one session per request, closed when it finishes."""
    db = local_session()
    try:
        yield db
    finally:
        db.close()
