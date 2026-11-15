"""Checkout tests: auth, idempotency, stock reservation, Razorpay order, quotas."""

from app import models
from app.services import razorpay as razorpay_mod
from tests._helpers import login, auth_headers, seed_variant

_order_counter = [0]


def _fake_create_order(amount_minor, currency, receipt, notes=None):
    _order_counter[0] += 1
    return {"id": f"order_rzp_{_order_counter[0]}", "amount": amount_minor, "currency": currency, "status": "created"}


def _add_to_cart(client, cookie, csrf, variant_id, quantity=1):
    r = client.post(
        "/api/cart",
        json={"variant_id": variant_id, "quantity": quantity},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_checkout_requires_auth(client, db):
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={"Idempotency-Key": "key-1"},
    )
    assert r.status_code == 401, r.text


def test_checkout_creates_order_and_reserves_stock(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="WH-1")
    _add_to_cart(client, cookie, csrf, variant_id, 2)

    r = client.post(
        "/api/orders",
        json={"shipping_address": {"line1": "1 Main St"}},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-1"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key_id"]
    assert body["amount_minor"] == 19998 + 0  # 2 * 9999, flat shipping default 0
    assert body["currency"] == "USD"
    assert body["razorpay_order_id"]

    order = (
        db.query(models.Order)
        .filter(models.Order.idempotency_key == "idem-1")
        .first()
    )
    assert order is not None
    assert order.status == "pending"
    assert order.razorpay_order_id == body["razorpay_order_id"]
    assert order.idempotency_payload_hash is not None
    # Stock reserved: reserved_stock = 2.
    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.reserved_stock == 2


def test_checkout_idempotent_replay_same_payload(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="WH-2")
    _add_to_cart(client, cookie, csrf, variant_id, 1)

    r1 = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-2"},
        cookies={"ecommerce_session": cookie},
    )
    assert r1.status_code == 201, r1.text
    order_id = r1.json()["order_id"]

    # Same key + same payload -> returns existing attempt, no new order.
    r2 = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-2"},
        cookies={"ecommerce_session": cookie},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["order_id"] == order_id
    assert db.query(models.Order).filter(models.Order.idempotency_key == "idem-2").count() == 1


def test_checkout_idempotent_changed_payload_409(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="WH-3")
    _add_to_cart(client, cookie, csrf, variant_id, 1)

    r1 = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-3"},
        cookies={"ecommerce_session": cookie},
    )
    assert r1.status_code == 201, r1.text

    # Same key, different payload -> 409.
    r2 = client.post(
        "/api/orders",
        json={"shipping_address": {"line1": "Different"}},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-3"},
        cookies={"ecommerce_session": cookie},
    )
    assert r2.status_code == 409, r2.text


def test_checkout_insufficient_stock_409(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=1, price_minor=9999, sku="WH-4")
    _add_to_cart(client, cookie, csrf, variant_id, 5)

    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-4"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "insufficient_stock"


def test_checkout_missing_idempotency_key_400(client, db):
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 400, r.text


def test_checkout_user_quota_429(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=100, price_minor=1000, sku="WH-5")
    _add_to_cart(client, cookie, csrf, variant_id, 1)

    # 5 admitted (quota 5/user/min).
    for i in range(5):
        # Each checkout consumes the cart; re-add for the next attempt.
        _add_to_cart(client, cookie, csrf, variant_id, 1)
        r = client.post(
            "/api/orders",
            json={"shipping_address": None},
            headers={**auth_headers(csrf), "Idempotency-Key": f"idem-q-{i}"},
            cookies={"ecommerce_session": cookie},
        )
        assert r.status_code in (201, 409), r.text

    # Replenish cart, then the 6th request is quota-exhausted (429).
    _add_to_cart(client, cookie, csrf, variant_id, 1)
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-q-6"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "rate_limit_exceeded"
    assert r.headers.get("Retry-After")


def test_checkout_empty_cart_400(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-empty"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 400, r.text
