"""003 products and product_variants

Revision ID: 003_products_variants
Revises: 002_categories
Create Date: 2026-11-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "003_products_variants"
down_revision = "002_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("delivery_type", sa.String(length=20), nullable=False, server_default="ship"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="fk_products_category_id_categories", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
    )
    op.create_index("ix_products_slug", "products", ["slug"], unique=True)
    op.create_index("ix_products_active", "products", ["active"], unique=False)
    op.create_index("ix_products_category_id", "products", ["category_id"], unique=False)
    op.create_check_constraint("ck_products_delivery_type", "products", "delivery_type IN ('ship', 'download', 'both')")

    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_variants_product_id_products", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_product_variants"),
    )
    op.create_index("ix_product_variants_sku", "product_variants", ["sku"], unique=True)
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"], unique=False)
    op.create_check_constraint("ck_variants_price_minor_nonneg", "product_variants", "price_minor >= 0")
    op.create_check_constraint("ck_variants_stock_nonneg", "product_variants", "stock >= 0")
    op.create_check_constraint("ck_variants_reserved_stock_nonneg", "product_variants", "reserved_stock >= 0")


def downgrade() -> None:
    op.drop_constraint("ck_variants_reserved_stock_nonneg", "product_variants", type_="check")
    op.drop_constraint("ck_variants_stock_nonneg", "product_variants", type_="check")
    op.drop_constraint("ck_variants_price_minor_nonneg", "product_variants", type_="check")
    op.drop_index("ix_product_variants_product_id", table_name="product_variants")
    op.drop_index("ix_product_variants_sku", table_name="product_variants")
    op.drop_table("product_variants")
    op.drop_check_constraint("ck_products_delivery_type", "products")
    op.drop_index("ix_products_category_id", table_name="products")
    op.drop_index("ix_products_active", table_name="products")
    op.drop_index("ix_products_slug", table_name="products")
    op.drop_table("products")
