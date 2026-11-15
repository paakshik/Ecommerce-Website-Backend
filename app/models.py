"""SQLAlchemy models for the ecommerce backend.

All tables enforce foreign keys, uniqueness, positive quantities, non-negative
amounts, and timezone-aware timestamps. Lookup columns are indexed.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="customer")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("role IN ('customer', 'admin')", name="ck_users_role"),
        Index("ix_users_email_lower", text("lower(email)")),
    )

    oauth_accounts = relationship("OAuthAccount", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="user", cascade="all, delete-orphan")
    wishlist_items = relationship("WishlistItem", back_populates="user", cascade="all, delete-orphan")
    download_grants = relationship("DownloadGrant", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    media_assets = relationship("MediaAsset", back_populates="uploader", cascade="all, delete-orphan")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    products = relationship("Product", back_populates="category", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ship")
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=text("now()")
    )

    __table_args__ = (
        CheckConstraint("delivery_type IN ('ship', 'download', 'both')", name="ck_products_delivery_type"),
        Index("ix_products_active", "active"),
        Index("ix_products_category_id", "category_id"),
    )

    category = relationship("Category", back_populates="products")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")
    media_assets = relationship("MediaAsset", back_populates="product", cascade="all, delete-orphan")
    reviews = relationship("Review", back_populates="product", cascade="all, delete-orphan")
    wishlist_items = relationship("WishlistItem", back_populates="product", cascade="all, delete-orphan")


class ProductVariant(Base):
    __tablename__ = "product_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    options: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="USD")
    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reserved_stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("price_minor >= 0", name="ck_variants_price_minor_nonneg"),
        CheckConstraint("stock >= 0", name="ck_variants_stock_nonneg"),
        CheckConstraint("reserved_stock >= 0", name="ck_variants_reserved_stock_nonneg"),
        Index("ix_variants_product_id", "product_id"),
        Index("ix_variants_sku", "sku", unique=True),
    )

    product = relationship("Product", back_populates="variants")
    order_items = relationship("OrderItem", back_populates="variant", cascade="all, delete-orphan")
    cart_items = relationship("CartItem", back_populates="variant", cascade="all, delete-orphan")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    cart_id: Mapped[Optional[int]] = mapped_column(ForeignKey("carts.id", ondelete="SET NULL"), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending", index=True)
    subtotal_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    shipping_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="USD")
    shipping_address: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    razorpay_order_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, nullable=True, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(200), unique=True, nullable=True, index=True)
    idempotency_payload_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reservation_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'fulfilled', 'cancelled', 'payment_review')",
            name="ck_orders_status",
        ),
        CheckConstraint("subtotal_minor >= 0", name="ck_orders_subtotal_nonneg"),
        CheckConstraint("shipping_minor >= 0", name="ck_orders_shipping_nonneg"),
        CheckConstraint("total_minor >= 0", name="ck_orders_total_nonneg"),
        Index("ix_orders_user_id", "user_id"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_razorpay_order_id", "razorpay_order_id", unique=True, postgresql_nulls_not_distinct=False),
        Index("ix_orders_idempotency_key", "idempotency_key", unique=True, postgresql_nulls_not_distinct=False),
    )

    user = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    delivery_type: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_pos"),
        CheckConstraint("price_minor >= 0", name="ck_order_items_price_nonneg"),
        CheckConstraint("delivery_type IN ('ship', 'download', 'both')", name="ck_order_items_delivery_type"),
        Index("ix_order_items_order_id", "order_id"),
        Index("ix_order_items_variant_id", "variant_id"),
    )

    order = relationship("Order", back_populates="items")
    variant = relationship("ProductVariant", back_populates="order_items")
    digital_asset = relationship("DigitalAsset", back_populates="order_item", uselist=False, cascade="all, delete-orphan")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    razorpay_payment_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    razorpay_order_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="USD")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=text("now()")
    )

    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_payments_amount_nonneg"),
        Index("ix_payments_order_id", "order_id"),
        Index("ix_payments_razorpay_payment_id", "razorpay_payment_id", unique=True),
    )

    order = relationship("Order", back_populates="payments")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_event_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_webhook_events_provider_event_id", "provider_event_id", unique=True),
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    csrf_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    oauth_state: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    oauth_code_verifier: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )

    user = relationship("User", back_populates="sessions")
    carts = relationship("Cart", back_populates="session", cascade="all, delete-orphan")


class Cart(Base):
    __tablename__ = "carts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=text("now()")
    )

    session = relationship("Session", back_populates="carts")
    items = relationship("CartItem", back_populates="cart", cascade="all, delete-orphan")


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_items_quantity_pos"),
        Index("ix_cart_items_cart_id", "cart_id"),
        Index("ix_cart_items_variant_id", "variant_id"),
    )

    cart = relationship("Cart", back_populates="items")
    variant = relationship("ProductVariant", back_populates="cart_items")


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    provider_user_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_oauth_accounts_user_id", "user_id"),
        Index("ix_oauth_accounts_provider_provider_user_id", "provider", "provider_user_id", unique=True),
    )

    user = relationship("User", back_populates="oauth_accounts")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    storage_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    uploader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_media_assets_byte_size_nonneg"),
        Index("ix_media_assets_product_id", "product_id"),
        Index("ix_media_assets_storage_key", "storage_key", unique=True),
    )

    product = relationship("Product", back_populates="media_assets")
    uploader = relationship("User", back_populates="media_assets")


class DigitalAsset(Base):
    __tablename__ = "digital_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    file_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_digital_assets_byte_size_nonneg"),
        Index("ix_digital_assets_order_item_id", "order_item_id", unique=True),
        Index("ix_digital_assets_file_key", "file_key", unique=True),
    )

    order_item = relationship("OrderItem", back_populates="digital_asset")
    download_grants = relationship("DownloadGrant", back_populates="digital_asset", cascade="all, delete-orphan")


class DownloadGrant(Base):
    __tablename__ = "download_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digital_asset_id: Mapped[int] = mapped_column(ForeignKey("digital_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_download_grants_user_id", "user_id"),
        Index("ix_download_grants_token_hash", "token_hash", unique=True),
        Index("ix_download_grants_expires_at", "expires_at"),
    )

    digital_asset = relationship("DigitalAsset", back_populates="download_grants")
    user = relationship("User", back_populates="download_grants")


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_wishlist_items_user_product", "user_id", "product_id", unique=True),
        Index("ix_wishlist_items_user_id", "user_id"),
        Index("ix_wishlist_items_product_id", "product_id"),
    )

    user = relationship("User", back_populates="wishlist_items")
    product = relationship("Product", back_populates="wishlist_items")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=text("now()")
    )

    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_reviews_rating_range"),
        Index("ix_reviews_user_product", "user_id", "product_id", unique=True),
        Index("ix_reviews_user_id", "user_id"),
        Index("ix_reviews_product_id", "product_id"),
    )

    user = relationship("User", back_populates="reviews")
    product = relationship("Product", back_populates="reviews")


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    scope: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_digest: Mapped[str] = mapped_column(String(128), primary_key=True)
    admitted: Mapped[list] = mapped_column(ARRAY(DateTime(timezone=True)), nullable=False, server_default=text("'{}'"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        Index("ix_rate_limit_buckets_expires_at", "expires_at"),
    )
