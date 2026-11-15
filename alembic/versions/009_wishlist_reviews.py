"""009 wishlist_items and reviews

Revision ID: 009_wishlist_reviews
Revises: 008_media_digital
Create Date: 2026-11-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "009_wishlist_reviews"
down_revision = "008_media_digital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_wishlist_items_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_wishlist_items_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_wishlist_items"),
    )
    op.create_index("ix_wishlist_items_user_product", "wishlist_items", ["user_id", "product_id"], unique=True)
    op.create_index("ix_wishlist_items_user_id", "wishlist_items", ["user_id"], unique=False)
    op.create_index("ix_wishlist_items_product_id", "wishlist_items", ["product_id"], unique=False)

    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_reviews_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_reviews_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
    )
    op.create_index("ix_reviews_user_product", "reviews", ["user_id", "product_id"], unique=True)
    op.create_index("ix_reviews_user_id", "reviews", ["user_id"], unique=False)
    op.create_index("ix_reviews_product_id", "reviews", ["product_id"], unique=False)
    op.create_check_constraint("ck_reviews_rating_range", "reviews", "rating >= 1 AND rating <= 5")


def downgrade() -> None:
    op.drop_constraint("ck_reviews_rating_range", "reviews", type_="check")
    op.drop_index("ix_reviews_product_id", table_name="reviews")
    op.drop_index("ix_reviews_user_id", table_name="reviews")
    op.drop_index("ix_reviews_user_product", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ix_wishlist_items_product_id", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_user_id", table_name="wishlist_items")
    op.drop_index("ix_wishlist_items_user_product", table_name="wishlist_items")
    op.drop_table("wishlist_items")
