"""008 media_assets, digital_assets and download_grants

Revision ID: 008_media_digital
Revises: 007_oauth_accounts
Create Date: 2026-11-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "008_media_digital"
down_revision = "007_oauth_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploader_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_media_assets_product_id_products", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], name="fk_media_assets_uploader_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_media_assets"),
    )
    op.create_index("ix_media_assets_storage_key", "media_assets", ["storage_key"], unique=True)
    op.create_index("ix_media_assets_product_id", "media_assets", ["product_id"], unique=False)
    op.create_index("ix_media_assets_uploader_id", "media_assets", ["uploader_id"], unique=False)
    op.create_check_constraint("ck_media_assets_byte_size_nonneg", "media_assets", "byte_size >= 0")

    op.create_table(
        "digital_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("file_key", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], name="fk_digital_assets_order_item_id_order_items", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_digital_assets"),
    )
    op.create_index("ix_digital_assets_order_item_id", "digital_assets", ["order_item_id"], unique=True)
    op.create_index("ix_digital_assets_file_key", "digital_assets", ["file_key"], unique=True)
    op.create_check_constraint("ck_digital_assets_byte_size_nonneg", "digital_assets", "byte_size >= 0")

    op.create_table(
        "download_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("digital_asset_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["digital_asset_id"], ["digital_assets.id"], name="fk_download_grants_digital_asset_id_digital_assets", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_download_grants_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_download_grants"),
    )
    op.create_index("ix_download_grants_token_hash", "download_grants", ["token_hash"], unique=True)
    op.create_index("ix_download_grants_user_id", "download_grants", ["user_id"], unique=False)
    op.create_index("ix_download_grants_expires_at", "download_grants", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_check_constraint("ck_digital_assets_byte_size_nonneg", "digital_assets")
    op.drop_index("ix_digital_assets_file_key", table_name="digital_assets")
    op.drop_index("ix_digital_assets_order_item_id", table_name="digital_assets")
    op.drop_table("digital_assets")
    op.drop_index("ix_download_grants_expires_at", table_name="download_grants")
    op.drop_index("ix_download_grants_user_id", table_name="download_grants")
    op.drop_index("ix_download_grants_token_hash", table_name="download_grants")
    op.drop_table("download_grants")
    op.drop_check_constraint("ck_media_assets_byte_size_nonneg", "media_assets")
    op.drop_index("ix_media_assets_uploader_id", table_name="media_assets")
    op.drop_index("ix_media_assets_product_id", table_name="media_assets")
    op.drop_index("ix_media_assets_storage_key", table_name="media_assets")
    op.drop_table("media_assets")
