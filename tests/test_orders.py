"""Order get and payment verification tests."""

from datetime import datetime, timedelta, timezone

from app import models
from app.services import razorpay as razorpay_mod
from tests._helpers import login, auth_headers, seed_variant, seed_digital_file


def _checkout(client, cookie, csrf, variant_id, quantity, idem_key, shipping=None):
    _add = client.post(
        "/api/cart",
        json={"variant_id": variant_id, "quantity": quantity},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert _add.status_code == 200, _add.text
    r = client.post(
        "/api/orders",
        json={"shipping_address": shipping},
        headers={**auth_headers(csrf), "Idempotency-Key": idem_key},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    return r.json()["order_id"]


def _fake_provider_payment(order_id, amount, currency="USD", status="captured"):
    return {"id": "pay_x", "order_id": order_id, "amount": amount, "currency": currency, "status": status}


def test_get_order_owner_and_forbidden(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="OW-1")
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-ow")

    # Owner gets the order.
    r = client.get(f"/api/orders/{order_id}", cookies={"ecommerce_session": cookie})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == order_id
    assert body["status"] == "pending"
    assert len(body["items"]) == 1
    assert body["items"][0]["product_name"] == "P"

    # Another user cannot see it.
    cookie2, csrf2 = login(client, "other@example.com", "Password123")
    r = client.get(f"/api/orders/{order_id}", cookies={"ecommerce_session": cookie2})
    assert r.status_code == 403, r.text

    # Not found.
    r = client.get("/api/orders/999999", cookies={"ecommerce_session": cookie})
    assert r.status_code == 404, r.text


def test_verify_payment_success_finalizes_stock(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: _fake_provider_payment("order_rzp", 19998, "USD", "captured"),
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="VP-1")
    order_id = _checkout(client, cookie, csrf, variant_id, 2, "idem-vp")

    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.reserved_stock == 2  # reserved at checkout

    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={
            "razorpay_payment_id": "pay_x",
            "razorpay_order_id": "order_rzp",
            "razorpay_signature": "sig",
        },
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "finalized"
    assert r.json()["status"] == "paid"

    order = db.query(models.Order).get(order_id)
    assert order.status == "paid"
    # Stock decreased AND reservation released: stock 5->3, reserved 2->0.
    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.stock == 3
    assert variant.reserved_stock == 0
    assert db.query(models.Payment).filter(models.Payment.razorpay_payment_id == "pay_x").first() is not None


def test_verify_payment_invalid_signature_400(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: False)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="VP-2")
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-vp2")

    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp", "razorpay_signature": "bad"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_signature"


def test_verify_payment_amount_mismatch_409(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: _fake_provider_payment("order_rzp", 5000, "USD", "captured"),
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="VP-3")
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-vp3")

    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "amount_mismatch"


def test_verify_payment_not_captured_409(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: _fake_provider_payment("order_rzp", 9999, "USD", "authorized"),
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="VP-4")
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-vp4")

    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "payment_not_captured"


def test_verify_payment_idempotent_already_paid(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: _fake_provider_payment("order_rzp", 9999, "USD", "captured"),
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="VP-5")
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-vp5")

    r1 = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r1.status_code == 200, r1.text

    # Replay: order already paid -> already_paid, no duplicate payment.
    r2 = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["action"] == "already_paid"
    assert db.query(models.Payment).filter(models.Payment.razorpay_payment_id == "pay_x").count() == 1


def test_digital_checkout_grants_download(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 1499, "currency": "USD", "status": "created"})
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: _fake_provider_payment("order_rzp", 1499, "USD", "captured"),
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, prod_id = seed_variant(db, delivery_type="download", stock=1000, price_minor=1499, sku="DG-1")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf", content_type="application/pdf", byte_size=500)
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-dg")

    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    # Digital-only cart: shipping 0.
    assert r.json()  # finalized
    order = db.query(models.Order).get(order_id)
    assert order.shipping_minor == 0
    # A download grant was created.
    assert db.query(models.DownloadGrant).filter(models.DownloadGrant.user_id == order.user_id).first() is not None


def test_list_orders_without_csrf_200(client, db, monkeypatch):
    """GET /api/orders must not require an X-CSRF-Token (safe read)."""
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="LC-1")
    order_id = _checkout(client, cookie, csrf, variant_id, 1, "idem-lc")

    # Session cookie present, but NO X-CSRF-Token header.
    r = client.get("/api/orders", cookies={"ecommerce_session": cookie})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "total" in body
    assert body["total"] >= 1
    assert any(o["id"] == order_id for o in body["items"])


def test_list_orders_date_filter(client, db):
    """created_after / created_before filter orders on created_at; total reflects the filtered count."""
    cookie, _ = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    base = datetime(2026, 1, 10, tzinfo=timezone.utc)
    for i in range(3):
        db.add(models.Order(
            user_id=user.id,
            email="buyer@example.com",
            status="pending",
            total_minor=1000,
            currency="USD",
            created_at=base + timedelta(days=i),
        ))
    db.commit()

    # On or after 2026-01-11 -> Jan 11 and Jan 12 (2 orders).
    r = client.get(
        "/api/orders",
        params={"created_after": "2026-01-11T00:00:00+00:00"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2, r.text
    assert len(r.json()["items"]) == 2

    # On or before 2026-01-11 -> Jan 10 and Jan 11 (2 orders).
    r = client.get(
        "/api/orders",
        params={"created_before": "2026-01-11T00:00:00+00:00"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2, r.text

    # Both bounds -> only Jan 11 (1 order).
    r = client.get(
        "/api/orders",
        params={"created_after": "2026-01-11T00:00:00+00:00", "created_before": "2026-01-11T23:59:59+00:00"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1, r.text
    assert len(r.json()["items"]) == 1


def test_idempotency_owner_scoped(client, db, monkeypatch):
    """Replaying another user's idempotency key must not return their order."""
    monkeypatch.setattr(razorpay_mod, "create_order", lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"})
    cookie_a, csrf_a = login(client, "buyer@example.com", "Password123")
    cookie_b, csrf_b = login(client, "other@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=1, price_minor=9999, sku="ISO-1")

    # User A checks out with idempotency key "shared-key" (reserves the last unit).
    client.post("/api/cart", json={"variant_id": variant_id, "quantity": 1}, headers=auth_headers(csrf_a), cookies={"ecommerce_session": cookie_a})
    r_a = client.post(
        "/api/orders",
        json={"shipping_address": "A"},
        headers={**auth_headers(csrf_a), "Idempotency-Key": "shared-key"},
        cookies={"ecommerce_session": cookie_a},
    )
    assert r_a.status_code == 201, r_a.text
    order_a = r_a.json()["order_id"]

    # User B checks out with their own cart and replays the SAME key.
    client.post("/api/cart", json={"variant_id": variant_id, "quantity": 1}, headers=auth_headers(csrf_b), cookies={"ecommerce_session": cookie_b})
    r_b = client.post(
        "/api/orders",
        json={"shipping_address": "B"},
        headers={**auth_headers(csrf_b), "Idempotency-Key": "shared-key"},
        cookies={"ecommerce_session": cookie_b},
    )

    # Owner-scoped lookup: user B must not receive user A's order. (Without the
    # fix, user B would get user A's order back here instead.)
    assert not (r_b.status_code == 201 and r_b.json().get("order_id") == order_a)


def test_list_orders_pagination_over_50(client, db):
    """Seeding >50 orders: total > 50 and limit/offset pages return correct subsets."""
    cookie, _ = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    base = datetime(2026, 2, 1, tzinfo=timezone.utc)
    for i in range(60):
        db.add(models.Order(
            user_id=user.id,
            email="buyer@example.com",
            status="pending",
            total_minor=1000 + i,
            currency="USD",
            created_at=base + timedelta(seconds=i),
        ))
    db.commit()

    r1 = client.get("/api/orders?limit=20&offset=0", cookies={"ecommerce_session": cookie})
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["total"] == 60, b1
    assert len(b1["items"]) == 20

    r2 = client.get("/api/orders?limit=20&offset=20", cookies={"ecommerce_session": cookie})
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["total"] == 60, b2
    assert len(b2["items"]) == 20

    ids1 = {o["id"] for o in b1["items"]}
    ids2 = {o["id"] for o in b2["items"]}
    assert ids1.isdisjoint(ids2)

    r3 = client.get("/api/orders?limit=20&offset=40", cookies={"ecommerce_session": cookie})
    assert r3.status_code == 200, r3.text
    assert len(r3.json()["items"]) == 20

    r4 = client.get("/api/orders?limit=20&offset=60", cookies={"ecommerce_session": cookie})
    assert r4.status_code == 200, r4.text
    assert r4.json()["total"] == 60
    assert r4.json()["items"] == []
