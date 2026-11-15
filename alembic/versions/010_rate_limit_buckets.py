"""010 rate_limit_buckets

Revision ID: 010_rate_limit_buckets
Revises: 009_wishlist_reviews
Create Date: 2026-11-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "010_rate_limit_buckets"
down_revision = "009_wishlist_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("subject_digest", sa.String(length=128), nullable=False),
        sa.Column("admitted", postgresql.ARRAY(sa.DateTime(timezone=True)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope", "subject_digest", name="pk_rate_limit_buckets"),
    )
    op.create_index("ix_rate_limit_buckets_expires_at", "rate_limit_buckets", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_rate_limit_buckets_expires_at", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
