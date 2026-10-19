"""Email verification and password reset (WTA-28)

users.email_verified_at, users.token_version, and the email_tokens table. Accounts that
exist before this revision are marked verified: they were created when no verification
existed, and locking them out of creating jobs would be a regression.

Revision ID: 0003_email_verification
Revises: 0002_users_and_job_owner
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_email_verification"
down_revision = "0002_users_and_job_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("token_version", sa.Integer(), server_default="0", nullable=False))
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")
    op.create_table(
        "email_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_table("email_tokens")
    op.drop_column("users", "token_version")
    op.drop_column("users", "email_verified_at")
