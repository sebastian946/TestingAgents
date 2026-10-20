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
    # WTA-32: pages discovered, waiting for the user to pick which ones get scenarios
    AWAITING_SELECTION = "awaiting_selection"
    DONE = "done"
    FAILED = "failed"


class User(Base):
    """An account (WTA-26). Jobs belong to a user and are only visible to that user."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-case
    password_hash: Mapped[str] = mapped_column(String(255))  # argon2 (security/auth.py)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Set when the user clicks the link in the verification email (WTA-28). Unverified users
    # can log in and look around but cannot create jobs: jobs cost money
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Carried in every JWT; bumping it (password reset) logs the user out everywhere
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    jobs: Mapped[list["Job"]] = relationship(back_populates="user")
    email_tokens: Mapped[list["EmailToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    """What the payment provider told us about a user's plan (WTA-30, ADR-09).

    One row per user, written only by the provider's webhook. `status` is the provider's
    own vocabulary (active, on_trial, past_due, cancelled, expired, ...); `plan` is ours
    (config/plans.py). A cancelled subscription keeps its plan until `current_period_end`.
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    plan: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(20))  # lemonsqueezy
    provider_subscription_id: Mapped[str] = mapped_column(String(64), unique=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(64))
    customer_portal_url: Mapped[str | None] = mapped_column(Text)  # where the user manages / cancels
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="subscription")


class EmailToken(Base):
    """A one-time token sent by email (WTA-28): verify the address, or reset the password.

    Only the SHA-256 of the token is stored, so a database leak does not hand out working
    links. A token is spent when `used_at` is set, and requesting a new one of the same
    purpose invalidates the earlier ones.
    """

    __tablename__ = "email_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # verify | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="email_tokens")


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Nullable only for rows created before accounts existed: the API never creates one
    # without an owner, and ownerless jobs are not visible through it
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        default=JobStatus.QUEUED,
    )
    description: Mapped[str | None] = mapped_column(String(500))
    # Authenticated crawl (WTA-29): the login page, kept for the record, and the encrypted
    # username/password (security/credentials.py), NULLed as soon as the crawl is over
    login_url: Mapped[str | None] = mapped_column(Text)
    login_credentials: Mapped[str | None] = mapped_column(Text)
    # Language of the scenarios and the report: "en" | "es" (config/languages.py)
    language: Mapped[str] = mapped_column(String(5), default="en", server_default="en")
    # WTA-32: explore first, then let the user choose the pages before spending on the LLM
    select_pages: Mapped[bool] = mapped_column(default=False, server_default="false")
    pages_crawled: Mapped[int] = mapped_column(default=0)
    total_scenarios: Mapped[int] = mapped_column(default=0)
    report_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User | None"] = relationship(back_populates="jobs")
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
    # WTA-32: whether the Designer covers this page; every page is selected unless the user
    # unticks it while the job is awaiting_selection
    selected: Mapped[bool] = mapped_column(default=True, server_default="true")
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
