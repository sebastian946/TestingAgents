"""Page selection (WTA-32): job.select_pages, pages.selected, and the awaiting_selection status

Revision ID: 0006_page_selection
Revises: 0005_subscriptions
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_page_selection"
down_revision = "0005_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL 12+ allows adding an enum value inside a transaction (it just cannot be used
    # in the same transaction, and this migration does not). Other engines have no enum type
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'awaiting_selection'")
    op.add_column("job", sa.Column("select_pages", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("pages", sa.Column("selected", sa.Boolean(), server_default="true", nullable=False))


def downgrade() -> None:
    op.drop_column("pages", "selected")
    op.drop_column("job", "select_pages")
    # The enum value stays: PostgreSQL cannot drop one, and no row uses it after the downgrade
