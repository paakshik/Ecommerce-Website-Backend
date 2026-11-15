"""Payment finalization, stock reconciliation, and payment_review handling.

Shared finalization service used by both the verification endpoint and Razorpay
webhooks. Idempotent: order-state locks plus unique payment/event IDs prevent
duplicate payments, stock changes, and download grants.
"""

from datetime import datetime, timedelta, timezone

from app import models
from app.config import get_settings
from app.services import email

settings = get_settings()

ORDER_TERMINAL = ("paid", "fulfilled", "cancelled", "payment_review")
DIGITAL_GRANT_TTL_SECONDS = settings.DOWNLOAD_LINK_TTL_SECONDS


def _is_digital_variant(variant: models.ProductVariant) -> bool:
    return variant is not None and variant.product.delivery_type == "download"


def _is_digital_only_order(order: models.Order) -> bool:
    return all(_is_digital_variant(item.variant) for item in order.items)


def _get_payment(db, order: models.Order, razorpay_payment_id: str):
    return (
        db.query(models.Payment)
        .filter(models.Payment.razorpay_payment_id == razorpay_payment_id)
        .first()
    )


def _grant_digital_access(db, order: models.Order, item: models.OrderItem):
    """Create a DigitalAsset (if missing) and a 15-minute DownloadGrant for a
    digital order item owned by the order's user.

    Returns the raw download token (or None when no grantable digital asset).
    """
    variant = item.variant
    if variant is None or not _is_digital_variant(variant):
        return None
    digital_asset = (
        db.query(models.DigitalAsset)
        .filter(models.DigitalAsset.order_item_id == item.id)
        .first()
    )
    if digital_asset is None:
        # The file key is supplied by the product's admin-uploaded digital asset.
        source = (
            db.query(models.MediaAsset)
            .filter(models.MediaAsset.product_id == variant.product_id)
            .order_by(models.MediaAsset.created_at.desc())
            .first()
        )
        if source is None:
            return None
        digital_asset = models.DigitalAsset(
            order_item_id=item.id,
            file_key=source.storage_key,
            content_type=source.content_type,
            byte_size=source.byte_size,
        )
        db.add(digital_asset)
        db.flush()
    # Avoid duplicate grants for the same order item + user.
    existing = (
        db.query(models.DownloadGrant)
        .filter(models.DownloadGrant.digital_asset_id == digital_asset.id)
        .filter(models.DownloadGrant.user_id == order.user_id)
        .filter(models.DownloadGrant.revoked_at.is_(None))
        .first()
    )
    if existing is not None:
        return None
    import secrets

    from app.auth import hash_token

    raw_token = secrets.token_urlsafe(24)
    grant = models.DownloadGrant(
        digital_asset_id=digital_asset.id,
        user_id=order.user_id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=DIGITAL_GRANT_TTL_SECONDS),
    )
    db.add(grant)
    db.flush()
    return raw_token


def finalize_payment(
    db,
    order: models.Order,
    razorpay_payment_id: str,
    amount_minor: int,
    currency: str,
    provider_status: str,
):
    """Atomically finalize a captured payment for an order.

    Returns (order, payment, action, raw_grant_token_or_None).
    action in {"finalized", "already_paid", "already_terminal", "payment_review"}.
    """
    # Order-state lock: only pending orders can move to paid.
    if order.status == "paid":
        return order, _get_payment(db, order, razorpay_payment_id), "already_paid", None
    if order.status in ORDER_TERMINAL:
        return order, None, "already_terminal", None

    # Record payment (unique razorpay_payment_id prevents duplicate rows).
    payment = _get_payment(db, order, razorpay_payment_id)
    if payment is None:
        payment = models.Payment(
            order_id=order.id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_order_id=order.razorpay_order_id,
            amount_minor=amount_minor,
            currency=currency,
            status=provider_status,
        )
        db.add(payment)
        db.flush()

    # Can we fulfill stock? (digital-only orders always can).
    can_fulfill = _is_digital_only_order(order)
    if not can_fulfill:
        # Assume physical stock can be fulfilled unless a variant proves short.
        can_fulfill = True
        for item in order.items:
            variant = item.variant
            if variant is None or variant.product.delivery_type == "download":
                continue
            # Free (unreserved) stock must cover this item's quantity.
            available = variant.stock - variant.reserved_stock
            if available < item.quantity:
                can_fulfill = False
                break

    if not can_fulfill:
        order.status = "payment_review"
        db.commit()
        email.log_receipt(order, payment)
        return order, payment, "payment_review", None

    # Mark the order paid.
    order.status = "paid"
    db.flush()

    # Finalize stock: decrease stock AND release the reservation atomically
    # for physical variants. Digital-only variants bypass stock gating.
    for item in order.items:
        variant = item.variant
        if variant is None or variant.product.delivery_type == "download":
            continue
        variant.stock = max(0, variant.stock - item.quantity)
        variant.reserved_stock = max(0, variant.reserved_stock - item.quantity)

    # Grant digital access for eligible digital items.
    raw_grant_token = None
    for item in order.items:
        variant = item.variant
        if variant is None or variant.product.delivery_type != "download":
            continue
        token = _grant_digital_access(db, order, item)
        if token:
            raw_grant_token = token

    # Consume the purchased cart snapshot: remove cart items that were purchased.
    # The cart is session-scoped; clear items matching purchased variants.
    _consume_cart(db, order)

    db.commit()
    # Log receipt after commit.
    email.log_receipt(order, payment)
    return order, payment, "finalized", raw_grant_token


def _consume_cart(db, order: models.Order) -> None:
    """Remove purchased items from the buyer's session cart.

    Preserves later additions made after checkout (snapshot semantics).
    """
    if order.user_id is None:
        return
    # Find the most recent cart for the user's latest session.
    session = (
        db.query(models.Session)
        .filter(models.Session.user_id == order.user_id)
        .order_by(models.Session.created_at.desc())
        .first()
    )
    if session is None:
        return
    cart = db.query(models.Cart).filter(models.Cart.session_id == session.id).first()
    if cart is None:
        return
    purchased_variant_ids = {item.variant_id for item in order.items}
    db.query(models.CartItem).filter(
        models.CartItem.cart_id == cart.id,
        models.CartItem.variant_id.in_(purchased_variant_ids),
    ).delete(synchronize_session=False)
    db.flush()
