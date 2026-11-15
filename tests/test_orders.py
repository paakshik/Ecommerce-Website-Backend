"""Order get and payment verification tests."""

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
