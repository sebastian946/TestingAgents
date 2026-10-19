"""Accounts (WTA-26): users table and job.user_id

Jobs created before this revision keep user_id NULL and are not visible through the API.

Revision ID: 0002_users_and_job_owner
Revises: 0001_initial_schema
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_users_and_job_owner"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.add_column("job", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_index("ix_job_user_id", "job", ["user_id"])
    op.create_foreign_key("job_user_id_fkey", "job", "users", ["user_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("job_user_id_fkey", "job", type_="foreignkey")
    op.drop_index("ix_job_user_id", table_name="job")
    op.drop_column("job", "user_id")
    op.drop_table("users")
