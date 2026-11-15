"""Razorpay webhook endpoint: signature verification, dedupe, finalization."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.services import payments, razorpay

router = APIRouter()

CAPTURE_EVENTS = {"payment.captured", "order.paid"}


@router.post("/webhooks/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
    x_razorpay_event_id: str | None = Header(default=None, alias="X-Razorpay-Event-Id"),
):
    """Razorpay webhook. Raw-body signature verification, idempotent by event id.

    Returns 200 for valid events (including already-processed). Non-2xx on
    transient failure so Razorpay retries.
    """
    # 1. Raw body + signature verification (no customer session required).
    body = await request.body()
    if isinstance(body, str):
        body = body.encode("utf-8")

    if not x_razorpay_signature or not razorpay.verify_webhook_signature(body, x_razorpay_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "invalid_signature", "message": "Invalid webhook signature"}},
        )

    # 2. Parse the event.
    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_payload", "message": "Invalid webhook payload"}},
        )

    event_id = x_razorpay_event_id or event.get("id")
    event_type = event.get("event")
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "missing_event_id", "message": "Missing Razorpay event id"}},
        )

    # 3. Dedupe: already-processed valid events return 200.
    existing = (
        db.query(models.WebhookEvent)
        .filter(models.WebhookEvent.provider_event_id == event_id)
        .first()
    )
    if existing is not None:
        return {"status": "already_processed"}

    # 4. Process the event.
    try:
        _process_event(db, event_type, event)
    except HTTPException:
        raise
    except Exception as exc:  # transient failure -> non-2xx so provider retries
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": "webhook_processing_failed", "message": str(exc)}},
        ) from exc

    # 5. Record the event as processed and commit before returning success.
    try:
        db.add(
            models.WebhookEvent(
                provider_event_id=event_id,
                event_type=event_type or "unknown",
                processed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except IntegrityError:
        # Concurrent duplicate: another worker already recorded this event.
        db.rollback()
        return {"status": "already_processed"}

    return {"status": "processed"}


def _process_event(db: Session, event_type: str, event: dict) -> None:
    payload = event.get("payload") or {}
    if event_type in CAPTURE_EVENTS:
        _handle_capture(db, payload)
    elif event_type == "payment.failed":
        _handle_failed(db, payload)
    # Other event types are acknowledged but not acted on.


def _extract_entity(payload: dict):
    for key in ("payment", "order"):
        entity = (payload.get(key) or {}).get("entity")
        if entity:
            return entity
    return None


def _handle_capture(db: Session, payload: dict) -> None:
    entity = _extract_entity(payload)
    if entity is None:
        return
    razorpay_order_id = entity.get("order_id")
    if not razorpay_order_id:
        return
    order = (
        db.query(models.Order)
        .filter(models.Order.razorpay_order_id == razorpay_order_id)
        .first()
    )
    if order is None:
        return
    # Finalization is idempotent; re-captures/order.paid after payment are no-ops.
    payment_id = entity.get("id")
    amount = entity.get("amount")
    currency = entity.get("currency")
    status = entity.get("status", "captured")
    if payment_id and amount is not None:
        payments.finalize_payment(
            db,
            order,
            payment_id,
            int(amount),
            currency or order.currency,
            status,
        )


def _handle_failed(db: Session, payload: dict) -> None:
    entity = _extract_entity(payload)
    if entity is None:
        return
    razorpay_order_id = entity.get("order_id")
    payment_id = entity.get("id")
    if not razorpay_order_id or not payment_id:
        return
    order = (
        db.query(models.Order)
        .filter(models.Order.razorpay_order_id == razorpay_order_id)
        .first()
    )
    if order is None:
        return
    # Track the failed payment without undoing a later capture.
    existing = (
        db.query(models.Payment)
        .filter(models.Payment.razorpay_payment_id == payment_id)
        .first()
    )
    if existing is None:
        db.add(
            models.Payment(
                order_id=order.id,
                razorpay_payment_id=payment_id,
                razorpay_order_id=razorpay_order_id,
                amount_minor=int(entity.get("amount", 0) or 0),
                currency=entity.get("currency", order.currency),
                status="failed",
            )
        )
        db.flush()
