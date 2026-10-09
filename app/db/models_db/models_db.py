import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.conn import Base

# JSONB on PostgreSQL; generic JSON on other engines (e.g. SQLite in tests)
JSONType = JSON().with_variant(JSONB, "postgresql")
# Exact decimal money in PostgreSQL; float in SQLite (tests), which has no decimal type
MoneyType = Numeric(12, 6).with_variant(Float(), "sqlite")


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
    description: Mapped[str | None] = mapped_column(String(500))
    pages_crawled: Mapped[int] = mapped_column(default=0)
    total_scenarios: Mapped[int] = mapped_column(default=0)
    report_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pages: Mapped[list["Page"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    scenarios: Mapped[list["Scenario"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    llm_calls: Mapped[list["LlmCall"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(255))
    page_type: Mapped[str | None] = mapped_column(String(100))
    screenshot_path: Mapped[str | None] = mapped_column(Text)
    elements: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # PageInfo inventory (WTA-11)
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


class LlmCall(Base):
    """One billed LLM API call (WTA-18). Job totals are SUMs over these rows.

    One row per call rather than counters on `job`: a job makes one call per page plus the
    self-healing retries, and failed calls (invalid output, refusal, cut off) are billed
    too. Keeping each call gives cost per page, per model and per attempt, and new agents
    (the Documenter) add rows without a schema change.
    """

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job.id", ondelete="CASCADE"), index=True)
    # NULL once a retried job wipes its pages: the call was still paid for, so it stays
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id", ondelete="SET NULL"), index=True)
    agent: Mapped[str] = mapped_column(String(50))  # designer (later: documenter)
    model: Mapped[str] = mapped_column(String(100))  # model that served the call
    attempt: Mapped[int]  # 1, or 2 for the self-healing retry
    outcome: Mapped[str] = mapped_column(String(20))  # ok | invalid | refusal | max_tokens
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_read_input_tokens: Mapped[int] = mapped_column(default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(default=0)
    # List price at the time of the call (prices change; history must not). NULL = unknown model
    cost_usd: Mapped[Decimal | None] = mapped_column(MoneyType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped["Job"] = relationship(back_populates="llm_calls")
