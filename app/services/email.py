"""Receipt logging.

Development mode logs receipts to the console. Real transactional email is
Phase 2 and intentionally deferred here.
"""

from app import models
from app.config import get_settings

settings = get_settings()


def log_receipt(order: models.Order, payment) -> None:
    """Log a receipt after commit. No-op in production until Phase 2 email."""
    if settings.EMAIL_MODE != "console":
        return
    print(
        "[RECEIPT] order_id=%s status=%s total_minor=%s currency=%s "
        "payment_id=%s email=%s"
        % (
            order.id,
            order.status,
            order.total_minor,
            order.currency,
            getattr(payment, "razorpay_payment_id", None),
            order.email,
        )
    )
