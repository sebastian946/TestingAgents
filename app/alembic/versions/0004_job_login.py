"""Authenticated crawl (WTA-29): job.login_url and the encrypted, short-lived job.login_credentials

Revision ID: 0004_job_login
Revises: 0003_email_verification
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_job_login"
down_revision = "0003_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job", sa.Column("login_url", sa.Text(), nullable=True))
    op.add_column("job", sa.Column("login_credentials", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "login_credentials")
    op.drop_column("job", "login_url")
