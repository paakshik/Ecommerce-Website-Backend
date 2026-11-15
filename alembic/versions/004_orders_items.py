"""004 orders and order_items

Revision ID: 004_orders_items
Revises: 003_products_variants
Create Date: 2026-11-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004_orders_items"
down_revision = "003_products_variants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("subtotal_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shipping_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("shipping_address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("razorpay_order_id", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("idempotency_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("reservation_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_orders_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"], unique=False)
    op.create_index("ix_orders_email", "orders", ["email"], unique=False)
    op.create_index("ix_orders_status", "orders", ["status"], unique=False)
    op.create_index("ix_orders_razorpay_order_id", "orders", ["razorpay_order_id"], unique=True)
    op.create_index("ix_orders_idempotency_key", "orders", ["idempotency_key"], unique=True)
    op.create_check_constraint("ck_orders_status", "orders", "status IN ('pending', 'paid', 'fulfilled', 'cancelled', 'payment_review')")
    op.create_check_constraint("ck_orders_subtotal_nonneg", "orders", "subtotal_minor >= 0")
    op.create_check_constraint("ck_orders_shipping_nonneg", "orders", "shipping_minor >= 0")
    op.create_check_constraint("ck_orders_total_nonneg", "orders", "total_minor >= 0")

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("delivery_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_order_items_order_id_orders", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], name="fk_order_items_variant_id_product_variants", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"], unique=False)
    op.create_index("ix_order_items_variant_id", "order_items", ["variant_id"], unique=False)
    op.create_check_constraint("ck_order_items_quantity_pos", "order_items", "quantity > 0")
    op.create_check_constraint("ck_order_items_price_nonneg", "order_items", "price_minor >= 0")
    op.create_check_constraint("ck_order_items_delivery_type", "order_items", "delivery_type IN ('ship', 'download', 'both')")


def downgrade() -> None:
    op.drop_constraint("ck_order_items_delivery_type", "order_items", type_="check")
    op.drop_constraint("ck_order_items_price_nonneg", "order_items", type_="check")
    op.drop_constraint("ck_order_items_quantity_pos", "order_items", type_="check")
    op.drop_index("ix_order_items_variant_id", table_name="order_items")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_check_constraint("ck_orders_total_nonneg", "orders")
    op.drop_check_constraint("ck_orders_shipping_nonneg", "orders")
    op.drop_check_constraint("ck_orders_subtotal_nonneg", "orders")
    op.drop_check_constraint("ck_orders_status", "orders")
    op.drop_index("ix_orders_idempotency_key", table_name="orders")
    op.drop_index("ix_orders_razorpay_order_id", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_email", table_name="orders")
    op.drop_index("ix_orders_user_id", table_name="orders")
    op.drop_table("orders")
