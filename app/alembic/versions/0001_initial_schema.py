"""Initial schema: job, pages, scenarios, llm_calls (the tables before accounts existed)

A database created with `db.init_db` before WTA-26 matches this revision exactly:
    uv run alembic stamp 0001_initial_schema
and then `upgrade head` applies the rest.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

job_status = postgresql.ENUM("queued", "running", "done", "failed", name="job_status", create_type=False)


def upgrade() -> None:
    job_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("language", sa.String(length=5), server_default="en", nullable=False),
        sa.Column("pages_crawled", sa.Integer(), nullable=False),
        sa.Column("total_scenarios", sa.Integer(), nullable=False),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("page_type", sa.String(length=100), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("elements", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pages_job_id", "pages", ["job_id"])
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("scenario_code", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=50), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"]),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "scenario_code", name="uq_scenario_code_per_job"),
    )
    op.create_index("ix_scenarios_job_id", "scenarios", ["job_id"])
    op.create_index("ix_scenarios_page_id", "scenarios", ["page_id"])
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=True),
        sa.Column("agent", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_job_id", "llm_calls", ["job_id"])
    op.create_index("ix_llm_calls_page_id", "llm_calls", ["page_id"])


def downgrade() -> None:
    op.drop_table("llm_calls")
    op.drop_table("scenarios")
    op.drop_table("pages")
    op.drop_table("job")
    job_status.drop(op.get_bind(), checkfirst=True)
