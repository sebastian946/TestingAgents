"""Billing (WTA-30, ADR-09): the subscriptions table written by the payment provider's webhook

Revision ID: 0005_subscriptions
Revises: 0004_job_login
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_subscriptions"
down_revision = "0004_job_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_subscription_id", sa.String(length=64), nullable=False),
        sa.Column("provider_customer_id", sa.String(length=64), nullable=True),
        sa.Column("customer_portal_url", sa.Text(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_subscription_id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
