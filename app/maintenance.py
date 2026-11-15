"""Maintenance: expired-session/bucket/download cleanup and payment/stock reconciliation.

Run periodically (e.g. via cron/scheduler). All changes are idempotent and
transactional.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.database import SessionLocal
from app import models


def _now():
    return datetime.now(timezone.utc)


def run_maintenance() -> None:
    """Release expired reservations, reconcile unresolved payments, cleanup stale data."""
    db = SessionLocal()
    try:
        now = _now()

        # 1. Release expired unpaid reservations.
        # Pending orders whose reservation window lapsed: return reserved stock.
        expired_orders = (
            db.query(models.Order)
            .filter(models.Order.status == "pending")
            .filter(models.Order.reservation_expiry.isnot(None))
            .filter(models.Order.reservation_expiry <= now)
            .all()
        )
        for order in expired_orders:
            for item in order.items:
                variant = item.variant
                if variant is None or variant.product.delivery_type == "download":
                    continue
                variant.reserved_stock = max(0, variant.reserved_stock - item.quantity)
                db.add(variant)
            db.flush()

        # 2. Reconcile unresolved payments.
        # Pending orders with a Razorpay order but no captured payment and an
        # expired reservation: move to payment_review for manual resolution.
        for order in expired_orders:
            has_capture = (
                db.query(models.Payment)
                .filter(models.Payment.order_id == order.id)
                .filter(models.Payment.status == "captured")
                .first()
            )
            if not has_capture:
                order.status = "payment_review"
                db.add(order)

        # 3. Cleanup expired download grants (revoke rather than delete for audit).
        db.query(models.DownloadGrant).filter(
            models.DownloadGrant.revoked_at.is_(None),
            models.DownloadGrant.expires_at <= now,
        ).update({models.DownloadGrant.revoked_at: now}, synchronize_session=False)

        # 4. Cleanup expired sessions and orphaned carts.
        db.query(models.Session).filter(models.Session.expires_at <= now).delete(
            synchronize_session=False
        )
        db.execute(
            text(
                """
                DELETE FROM carts c
                USING sessions s
                WHERE c.session_id = s.id AND s.expires_at <= :now
                """
            ),
            {"now": now},
        )

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_maintenance()
    print("Maintenance complete.")
