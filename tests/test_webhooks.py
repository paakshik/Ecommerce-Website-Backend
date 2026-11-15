"""Razorpay webhook tests: signature, dedupe, capture, failed, out-of-order."""

from app import models
from app.services import razorpay as razorpay_mod
from tests._helpers import seed_variant


def _order(db, status="pending", razorpay_order_id="order_rzp_wh", user_email="b@example.com"):
    user = models.User(email=user_email, password_hash="x", role="customer")
    db.add(user)
    db.flush()
    order = models.Order(
        user_id=user.id, email=user.email, status=status,
        subtotal_minor=9999, shipping_minor=0, total_minor=9999,
        currency="USD", razorpay_order_id=razorpay_order_id,
        reservation_expiry=None,
    )
    db.add(order)
    db.flush()
    return order, user


def _webhook_payload(event_id, event_type, razorpay_order_id, amount=9999, currency="USD", payment_status="captured", payment_id="pay_wh"):
    return {
        "id": event_id,
        "event": event_type,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": razorpay_order_id,
                    "amount": amount,
                    "currency": currency,
                    "status": payment_status,
                }
            },
            "order": {"entity": {"id": razorpay_order_id}},
        },
    }


def test_webhook_capture_with_payload(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    order, _ = _order(db)
    import json

    body = json.dumps(_webhook_payload("evt_cap_1", "payment.captured", order.razorpay_order_id, amount=order.total_minor))
    r = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={
            "X-Razorpay-Signature": "sig",
            "X-Razorpay-Event-Id": "evt_cap_1",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200, r.text
    db.expire(order)
    order = db.query(models.Order).get(order.id)
    assert order.status == "paid"
    # Payment recorded.
    assert db.query(models.Payment).filter(models.Payment.order_id == order.id).first() is not None


def test_webhook_dedupe_returns_200(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    order, _ = _order(db)
    import json

    # Pre-record the event as already processed.
    db.add(models.WebhookEvent(provider_event_id="evt_dup_1", event_type="payment.captured", processed_at=None))
    db.commit()

    body = json.dumps(_webhook_payload("evt_dup_1", "payment.captured", order.razorpay_order_id, amount=order.total_minor))
    r = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt_dup_1", "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "already_processed"
    # Order remains pending (not re-processed).
    db.expire(order)
    order = db.query(models.Order).get(order.id)
    assert order.status == "pending"


def test_webhook_invalid_signature_401(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: False)
    r = client.post(
        "/api/webhooks/razorpay",
        content=b"{}",
        headers={"X-Razorpay-Signature": "bad", "Content-Type": "application/json"},
    )
    assert r.status_code == 401, r.text


def test_webhook_payment_failed_tracked_without_undo(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    # A previously captured payment exists.
    order, _ = _order(db, status="paid")
    db.add(models.Payment(
        order_id=order.id, razorpay_payment_id="pay_captured",
        razorpay_order_id=order.razorpay_order_id, amount_minor=9999,
        currency="USD", status="captured",
    ))
    db.commit()
    import json

    body = json.dumps(_webhook_payload("evt_fail_1", "payment.failed", order.razorpay_order_id, payment_id="pay_fail_1"))
    r = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt_fail_1", "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    # Order stays paid; failed payment recorded separately.
    db.expire(order)
    order = db.query(models.Order).get(order.id)
    assert order.status == "paid"
    failed = db.query(models.Payment).filter(models.Payment.razorpay_payment_id == "pay_fail_1").first()
    assert failed is not None
    assert failed.status == "failed"


def test_webhook_out_of_order_idempotent(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    order, _ = _order(db)
    import json

    # order.paid arrives first, then payment.captured for the same payment.
    for evt in ("evt_oo_1", "evt_oo_2"):
        etype = "order.paid" if evt == "evt_oo_1" else "payment.captured"
        body = json.dumps(_webhook_payload(evt, etype, order.razorpay_order_id, amount=order.total_minor))
        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": evt, "Content-Type": "application/json"},
        )
        assert r.status_code == 200, r.text
    db.expire(order)
    order = db.query(models.Order).get(order.id)
    assert order.status == "paid"
    assert db.query(models.Payment).filter(models.Payment.order_id == order.id).count() == 1
