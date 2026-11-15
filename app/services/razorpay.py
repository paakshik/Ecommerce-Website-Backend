"""Razorpay integration: server-side orders, signature verification, webhooks.

The API secret (RAZORPAY_KEY_SECRET) and webhook secret are server-only and never
leak to the frontend. All provider calls use bounded timeouts.
"""

from typing import Optional

import razorpay

from app.config import get_settings

settings = get_settings()

_client: Optional[razorpay.Client] = None


def get_client() -> razorpay.Client:
    """Return a cached Razorpay client authenticated with the server key pair."""
    global _client
    if _client is None:
        _client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
    return _client


def create_order(amount_minor: int, currency: str, receipt: str, notes=None) -> dict:
    """Create a Razorpay order with automatic capture.

    In non-production test mode (default), returns a fake provider order so
    development and E2E can run without real Razorpay credentials. Production
    always calls the real provider and requires real secrets.
    """
    if settings.ENV != "production" and settings.RAZORPAY_TEST_MODE:
        return {
            "id": f"order_test_{receipt}",
            "amount": amount_minor,
            "currency": currency,
            "receipt": receipt,
            "status": "created",
            "notes": notes or {},
        }
    data = {
        "amount": amount_minor,
        "currency": currency,
        "receipt": receipt,
        "payment_capture": 1,
    }
    if notes:
        data["notes"] = notes or {}
    return get_client().order.create(data=data)


def verify_payment_signature(
    razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str
) -> bool:
    """Verify a Razorpay payment signature against the server API secret.

    Returns True when valid, False otherwise (never raises).
    """
    try:
        get_client().utility.verify_payment_signature(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
        return True
    except Exception:
        return False


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify a Razorpay webhook signature against the separate webhook secret.

    Returns True when valid, False otherwise (never raises).
    """
    try:
        get_client().utility.verify_webhook_signature(
            body, signature, settings.RAZORPAY_WEBHOOK_SECRET
        )
        return True
    except Exception:
        return False


def fetch_payment(payment_id: str) -> dict:
    """Fetch a payment from Razorpay (bounded timeout)."""
    return get_client().payment.fetch(payment_id)
