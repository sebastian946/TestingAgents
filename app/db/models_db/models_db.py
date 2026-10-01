import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.conn import Base

# JSONB on PostgreSQL; generic JSON on other engines (e.g. SQLite in tests)
JSONType = JSON().with_variant(JSONB, "postgresql")


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        default=JobStatus.QUEUED,
    )
    pages_crawled: Mapped[int] = mapped_column(default=0)
    total_scenarios: Mapped[int] = mapped_column(default=0)
    report_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages: Mapped[list["Page"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(255))
    page_type: Mapped[str | None] = mapped_column(String(100))
    screenshot_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped["Job"] = relationship(back_populates="pages")
    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("job_id", "scenario_code", name="uq_scenario_code_per_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    scenario_code: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(255))
    steps: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    expected_result: Mapped[str] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str | None] = mapped_column(String(50))

    job: Mapped["Job"] = relationship(back_populates="scenarios")
    page: Mapped["Page"] = relationship(back_populates="scenarios")
