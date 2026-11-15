"""011 oauth state columns on sessions

Revision ID: 011_oauth_state_columns
Revises: 010_rate_limit_buckets
Create Date: 2026-11-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "011_oauth_state_columns"
down_revision = "010_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("oauth_state", sa.String(length=200), nullable=True))
    op.add_column("sessions", sa.Column("oauth_code_verifier", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "oauth_code_verifier")
    op.drop_column("sessions", "oauth_state")
