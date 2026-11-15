"""Orders and checkout router: idempotent checkout, stock reservation, Razorpay."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, joinedload

from app import auth as auth_mod, models
from app.database import get_db
from app import rate_limits
from app.dependencies import csrf_valid, current_user, rate_limit_checkout_ip
from app.config import get_settings
from app.schemas import (
    CartOut,
    CheckoutResponse,
    OrderCreate,
    OrderItemOut,
    OrderOut,
    PaymentVerify,
)
from app.services import payments, razorpay

settings = get_settings()

router = APIRouter()


def _payload_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _get_user_cart(db: Session, user: models.User):
    """Return the buyer's current session cart (latest session with a cart)."""
    session = (
        db.query(models.Session)
        .filter(models.Session.user_id == user.id)
        .order_by(models.Session.created_at.desc())
        .first()
    )
    if session is None:
        return None
    return db.query(models.Cart).filter(models.Cart.session_id == session.id).first()


@router.post(
    "/orders",
    status_code=status.HTTP_201_CREATED,
    response_model=CheckoutResponse,
)
def create_order(
    response: Response,
    request: Request,
    payload: OrderCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _ip_quota: None = Depends(rate_limit_checkout_ip),
):
    """Create a checkout order from the current cart.

    Quotas are consumed before any order/provider side effects, including
    idempotent replays. Same key + same payload returns the existing attempt;
    same key + changed payload returns 409.
    """
    csrf_valid(request, db, session_cookie, x_csrf_token)

    # Checkout user quota (enforced directly; rate_limit_checkout_user requires
    # an explicit user argument). Consumed before any side effect, including
    # idempotent replays.
    limit, window = rate_limits.QUOTAS["checkout_user"]
    allowed, retry = rate_limits.check_bucket(
        db, "checkout_user", rate_limits._digest(str(user.id)), limit, window
    )
    if not allowed:
        raise rate_limits.rate_limit_response(retry)

    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "idempotency_key_required", "message": "Idempotency-Key header is required"}},
        )

    body = {
        "shipping_address": payload.shipping_address,
    }
    payload_hash = _payload_hash(body)

    # Idempotency lookup (quota already consumed above).
    existing = (
        db.query(models.Order)
        .filter(models.Order.idempotency_key == idempotency_key)
        .first()
    )
    if existing is not None:
        if existing.idempotency_payload_hash != payload_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "idempotency_conflict", "message": "Same idempotency key with a different payload"}},
            )
        return _checkout_response(existing)

    cart = _get_user_cart(db, user)
    if cart is None or not cart.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "cart_empty", "message": "Cart is empty"}},
        )

    # Calculate totals from current catalog prices and reserve physical stock
    # in a single transaction with row locks.
    subtotal = 0
    all_digital = True
    order_items_input = []
    for ci in cart.items:
        variant = (
            db.query(models.ProductVariant)
            .filter(models.ProductVariant.id == ci.variant_id)
            .with_for_update()
            .first()
        )
        if variant is None or not variant.active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": "variant_unavailable", "message": f"Variant {ci.variant_id} unavailable"}},
            )
        product = variant.product
        line_total = variant.price_minor * ci.quantity
        subtotal += line_total
        if product.delivery_type != "download":
            all_digital = False
            available = variant.stock - variant.reserved_stock
            if available < ci.quantity:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"error": {"code": "insufficient_stock", "message": f"Insufficient stock for {variant.sku}"}},
                )
            variant.reserved_stock += ci.quantity
        order_items_input.append((variant, product, ci.quantity, line_total))
        db.add(variant)

    shipping = 0 if all_digital or not order_items_input else settings.SHIPPING_FLAT_RATE_MINOR
    total = subtotal + shipping

    # Create the Razorpay order before persisting, so its ID is stored.
    receipt = f"ord_{user.id}_{int(datetime.now(timezone.utc).timestamp())}"
    try:
        provider_order = razorpay.create_order(
            amount_minor=total,
            currency=settings.STORE_CURRENCY,
            receipt=receipt,
            notes={"user_id": str(user.id)},
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "provider_order_failed", "message": "Payment provider order creation failed"}},
        ) from exc

    razorpay_order_id = provider_order["id"]

    order = models.Order(
        user_id=user.id,
        email=user.email,
        status="pending",
        subtotal_minor=subtotal,
        shipping_minor=shipping,
        total_minor=total,
        currency=settings.STORE_CURRENCY,
        shipping_address=payload.shipping_address,
        razorpay_order_id=razorpay_order_id,
        idempotency_key=idempotency_key,
        idempotency_payload_hash=payload_hash,
        reservation_expiry=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(order)
    db.flush()

    for variant, product, quantity, line_total in order_items_input:
        db.add(
            models.OrderItem(
                order_id=order.id,
                variant_id=variant.id,
                quantity=quantity,
                price_minor=variant.price_minor,
                product_name=product.name,
                delivery_type=product.delivery_type,
            )
        )

    db.commit()
    db.refresh(order)
    return _checkout_response(order)


def _checkout_response(order: models.Order) -> CheckoutResponse:
    return CheckoutResponse(
        order_id=order.id,
        razorpay_order_id=order.razorpay_order_id,
        key_id=settings.RAZORPAY_KEY_ID,
        amount_minor=order.total_minor,
        currency=order.currency,
    )


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Order not found"}},
        )
    if order.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "forbidden", "message": "Not your order"}},
        )
    return _order_out(db, order)


def _order_out(db: Session, order: models.Order) -> OrderOut:
    items = [
        OrderItemOut(
            id=it.id,
            variant_id=it.variant_id,
            product_name=it.product_name,
            quantity=it.quantity,
            price_minor=it.price_minor,
            delivery_type=it.delivery_type,
        )
        for it in order.items
    ]
    downloadable = [
        it.id for it in order.items if it.variant.product.delivery_type == "download"
    ]
    return OrderOut(
        id=order.id,
        status=order.status,
        subtotal_minor=order.subtotal_minor,
        shipping_minor=order.shipping_minor,
        total_minor=order.total_minor,
        currency=order.currency,
        email=order.email,
        razorpay_order_id=order.razorpay_order_id,
        payment_status=_latest_payment_status(db, order),
        created_at=order.created_at,
        items=items,
        downloadable_item_ids=downloadable,
    )


def _latest_payment_status(db: Session, order: models.Order):
    payment = (
        db.query(models.Payment)
        .filter(models.Payment.order_id == order.id)
        .order_by(models.Payment.created_at.desc())
        .first()
    )
    return payment.status if payment else None


@router.post("/orders/{order_id}/verify-payment")
def verify_payment(
    order_id: int,
    payload: PaymentVerify,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    """Verify a Razorpay payment signature, confirm with the provider, and
    finalize the order (owner only)."""
    csrf_valid(request, db, session_cookie, x_csrf_token)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Order not found"}},
        )
    if order.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "forbidden", "message": "Not your order"}},
        )
    if not order.razorpay_order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "no_payment", "message": "Order has no payment provider record"}},
        )

    # 1. Verify the signature binds payment + order to our secret.
    if not razorpay.verify_payment_signature(
        order.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_signature", "message": "Payment signature verification failed"}},
        )

    # 2. Confirm captured status, amount, currency, and order binding with provider.
    try:
        provider_payment = razorpay.fetch_payment(payload.razorpay_payment_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"code": "provider_fetch_failed", "message": "Could not confirm payment with provider"}},
        ) from exc

    if str(provider_payment.get("order_id")) != str(order.razorpay_order_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "order_binding_mismatch", "message": "Payment is not bound to this order"}},
        )
    if int(provider_payment.get("amount", -1)) != order.total_minor:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "amount_mismatch", "message": "Payment amount does not match order total"}},
        )
    if str(provider_payment.get("currency", "")).upper() != str(order.currency).upper():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "currency_mismatch", "message": "Payment currency does not match order"}},
        )
    if provider_payment.get("status") != "captured":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "payment_not_captured", "message": "Payment is not yet captured"}},
        )

    # 3. Finalize (idempotent): record payment, mark paid, finalize stock,
    #    grant digital access, consume cart snapshot, log receipt.
    order, payment, action, _token = payments.finalize_payment(
        db,
        order,
        payload.razorpay_payment_id,
        order.total_minor,
        order.currency,
        provider_payment.get("status", "captured"),
    )

    return {
        "order_id": order.id,
        "status": order.status,
        "action": action,
        "payment_id": payload.razorpay_payment_id,
        "amount_minor": order.total_minor,
        "currency": order.currency,
    }
