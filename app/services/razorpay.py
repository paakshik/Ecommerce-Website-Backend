"""Razorpay integration: server-side orders, signature verification, webhooks.

Provider modes (controlled by RAZORPAY_TEST_MODE and the Razorpay key prefix):

* ``live``     (RAZORPAY_TEST_MODE = false): real Razorpay production credentials.
* ``test``     (RAZORPAY_TEST_MODE = true, key starts with ``rzp_test_``): real
  Razorpay TEST credentials via the SDK. Genuine test payments use real
  provider-created test orders -- never fabricated ids.
* ``mock``     (RAZORPAY_TEST_MODE = true, key does not look like a real key):
  fabricated ``order_test_...`` ids for local development without any credentials.
  Mock mode is prohibited in production and its ids must never reach a real
  checkout widget in production.

The API secret (RAZORPAY_KEY_SECRET) and webhook secret are server-only and never
leak to the frontend. All provider calls use bounded timeouts.
"""

from typing import Optional

import razorpay
import requests
from razorpay.errors import BadRequestError, GatewayError, ServerError, SignatureVerificationError

from app.config import get_settings

settings = get_settings()

_client: Optional[razorpay.Client] = None

RAZORPAY_TEST_KEY_PREFIX = "rzp_test_"
RAZORPAY_LIVE_KEY_PREFIX = "rzp_live_"

# Returned to the client in mock/dev mode only; never a real provider order id.
MOCK_ORDER_ID_PREFIX = "order_test_"


class RazorpayError(Exception):
    """Razorpay provider failure that must not leak secrets to the client."""


def _key_starts_with(key_id: Optional[str], prefix: str) -> bool:
    return bool(key_id) and key_id.startswith(prefix)


def provider_mode() -> str:
    """Return the active provider mode: ``live``, ``test``, or ``mock``."""
    if settings.RAZORPAY_TEST_MODE:
        if _key_starts_with(settings.RAZORPAY_KEY_ID, RAZORPAY_TEST_KEY_PREFIX):
            return "test"
        return "mock"
    return "live"


class ProviderSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (5, 20))
        return super().request(method, url, **kwargs)


def get_client() -> razorpay.Client:
    """Return a cached Razorpay client authenticated with the server key pair."""
    global _client
    if _client is None:
        _client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            session=ProviderSession(),
        )
    return _client


def verify_razorpay_credentials() -> None:
    """Validate that the configured Razorpay key pair authenticates with the provider.

    Makes a lightweight provider call (listing orders) that requires a valid key
    pair. Raises :class:`RazorpayError` with a generic message when authentication
    fails. Never prints or returns secret values.

    Intended to be invoked at application startup / redeploy so an invalid
    credential pair fails fast instead of failing mid-checkout. Changing
    credentials requires a backend restart/redeployment because the client is
    cached after first use.
    """
    if provider_mode() == "mock":
        # No real provider to authenticate against in mock/dev mode.
        return
    try:
        get_client().order.all({"count": 1})
    except BadRequestError as exc:
        # 401 / invalid credentials from Razorpay.
        raise RazorpayError("Razorpay credentials could not be verified") from exc
    except Exception as exc:  # network or other provider failure
        raise RazorpayError("Razorpay credentials could not be verified") from exc


def create_order(amount_minor: int, currency: str, receipt: str, notes=None) -> dict:
    """Create a Razorpay order with automatic capture.

    In ``test`` mode (real ``rzp_test_`` credentials) and ``live`` mode this calls
    the real provider via the SDK and returns a provider-created order id.
    In ``mock`` mode (local dev without credentials) it returns a fabricated
    ``order_test_...`` id; mock mode is prohibited in production.
    """
    mode = provider_mode()

    if mode == "mock":
        if settings.ENV == "production":
            raise RazorpayError(
                "Razorpay is not configured with real credentials in production"
            )
        return {
            "id": f"{MOCK_ORDER_ID_PREFIX}{receipt}",
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
    try:
        return get_client().order.create(data=data)
    except BadRequestError as exc:
        # Includes authentication failures ("authentication failed") from Razorpay.
        raise RazorpayError("Payment provider order creation failed") from exc
    except (GatewayError, ServerError) as exc:
        raise RazorpayError("Payment provider order creation failed") from exc
    except Exception as exc:
        raise RazorpayError("Payment provider order creation failed") from exc


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
    except SignatureVerificationError:
        return False
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
    try:
        return get_client().payment.fetch(payment_id)
    except BadRequestError as exc:
        raise RazorpayError("Payment fetch failed") from exc
    except (GatewayError, ServerError) as exc:
        raise RazorpayError("Payment fetch failed") from exc
    except Exception as exc:
        raise RazorpayError("Payment fetch failed") from exc
