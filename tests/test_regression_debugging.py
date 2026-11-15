"""Regression tests for Debugging-agent fixes.

F1: Razorpay signature verification uses the real SDK (no mocking) with locally
generated valid/invalid HMAC signatures.
F2: can_fulfill must not send physical orders with sufficient stock to payment_review.
F7: HTTP rate-limit contract on register (429 + Retry-After + no rejected-request side effect).
"""

import hashlib
import hmac

from app import models
from app.services import payments, razorpay as razorpay_mod


def _setup(db):
    user = models.User(email="b@example.com", password_hash="x", role="customer")
    db.add(user)
    db.flush()
    return user


_cat_counter = [0]


def _make_order(db, user, status="pending", total_minor=2000):
    order = models.Order(
        user_id=user.id, email=user.email, status=status,
        subtotal_minor=total_minor, shipping_minor=0, total_minor=total_minor, currency="USD",
        razorpay_order_id="order_rzp",
    )
    db.add(order)
    db.flush()
    return order


def _make_variant(db, product, stock, reserved, sku, delivery_type="ship"):
    v = models.ProductVariant(
        product_id=product.id, sku=sku, price_minor=1000, currency="USD",
        stock=stock, reserved_stock=reserved, active=True,
    )
    db.add(v)
    db.flush()
    return v


def _seed_category_product(db, delivery_type="ship"):
    _cat_counter[0] += 1
    n = _cat_counter[0]
    cat = models.Category(name=f"Cat {n}", slug=f"cat-{n}")
    db.add(cat)
    db.flush()
    prod = models.Product(
        name=f"P {n}", slug=f"p-{n}", delivery_type=delivery_type,
        category_id=cat.id, active=True,
    )
    db.add(prod)
    db.flush()
    return prod


# ---------- F1: Razorpay signature (real SDK, locally generated signatures) ----------

def test_verify_payment_signature_real_sdk_valid_invalid(monkeypatch):
    secret = "regression-test-secret"
    monkeypatch.setattr(razorpay_mod.settings, "RAZORPAY_KEY_SECRET", secret)
    monkeypatch.setattr(razorpay_mod.settings, "RAZORPAY_KEY_ID", "regression-test-key")
    monkeypatch.setattr(razorpay_mod, "_client", None)
    order_id, payment_id = "order_regression", "pay_regression"
    valid_sig = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    assert razorpay_mod.verify_payment_signature(order_id, payment_id, valid_sig) is True
    assert razorpay_mod.verify_payment_signature(order_id, payment_id, "0" * 64) is False
    assert razorpay_mod.verify_payment_signature("order_other", payment_id, valid_sig) is False


# ---------- F2: can_fulfill must not misclassify sufficient physical stock ----------

def test_physical_sufficient_stock_not_payment_review(client, db):
    user = _setup(db)
    prod = _seed_category_product(db, "ship")
    order = _make_order(db, user)
    v = _make_variant(db, prod, stock=10, reserved=2, sku="S-1")
    db.add(models.OrderItem(order_id=order.id, variant_id=v.id, quantity=2, price_minor=1000,
                            product_name="P", delivery_type="ship"))
    db.flush()
    order, payment, action, token = payments.finalize_payment(db, order, "pay_1", 2000, "USD", "captured")
    assert action == "finalized"
    assert order.status == "paid"
    assert v.stock == 8
    assert v.reserved_stock == 0


def test_physical_insufficient_stock_payment_review(client, db):
    user = _setup(db)
    prod = _seed_category_product(db, "ship")
    order = _make_order(db, user)
    v = _make_variant(db, prod, stock=1, reserved=0, sku="S-2")
    db.add(models.OrderItem(order_id=order.id, variant_id=v.id, quantity=2, price_minor=1000,
                            product_name="P", delivery_type="ship"))
    db.flush()
    order, payment, action, token = payments.finalize_payment(db, order, "pay_2", 2000, "USD", "captured")
    assert action == "payment_review"
    assert order.status == "payment_review"
    assert v.stock == 1
    assert v.reserved_stock == 0


def test_digital_only_finalized_stock_untouched(client, db):
    user = _setup(db)
    prod = _seed_category_product(db, "download")
    ma = models.MediaAsset(product_id=prod.id, storage_key="digital/file.bin",
                           content_type="application/octet-stream", byte_size=100, uploader_id=None)
    db.add(ma)
    db.flush()
    order = _make_order(db, user, total_minor=1499)
    v = _make_variant(db, prod, stock=0, reserved=0, sku="S-3", delivery_type="download")
    db.add(models.OrderItem(order_id=order.id, variant_id=v.id, quantity=5, price_minor=1499,
                            product_name="P", delivery_type="download"))
    db.flush()
    order, payment, action, token = payments.finalize_payment(db, order, "pay_3", 1499, "USD", "captured")
    assert action == "finalized"
    assert order.status == "paid"
    assert v.stock == 0  # digital: stock untouched


def test_mixed_physical_digital_sufficient_stock_finalized(client, db):
    user = _setup(db)
    ship_prod = _seed_category_product(db, "ship")
    dl_prod = _seed_category_product(db, "download")
    ma = models.MediaAsset(product_id=dl_prod.id, storage_key="digital/m.bin",
                           content_type="application/octet-stream", byte_size=100, uploader_id=None)
    db.add(ma)
    db.flush()
    order = _make_order(db, user)
    sv = _make_variant(db, ship_prod, stock=10, reserved=0, sku="S-4")
    dv = _make_variant(db, dl_prod, stock=0, reserved=0, sku="S-5", delivery_type="download")
    db.add(models.OrderItem(order_id=order.id, variant_id=sv.id, quantity=2, price_minor=1000,
                            product_name="P", delivery_type="ship"))
    db.add(models.OrderItem(order_id=order.id, variant_id=dv.id, quantity=1, price_minor=1499,
                            product_name="D", delivery_type="download"))
    db.flush()
    order, payment, action, token = payments.finalize_payment(db, order, "pay_4", 3499, "USD", "captured")
    assert action == "finalized"
    assert order.status == "paid"
    assert sv.stock == 8


def test_repeat_payment_already_paid_idempotent(client, db):
    user = _setup(db)
    order = _make_order(db, user, status="paid")
    order, payment, action, token = payments.finalize_payment(db, order, "pay_5", 2000, "USD", "captured")
    assert action == "already_paid"
    assert order.status == "paid"


# ---------- F7: HTTP rate-limit contract on register ----------

def test_register_http_429_retry_after_no_side_effect(client, db):
    from tests._helpers import bootstrap, auth_headers
    email = "reg-rate@example.com"
    for i in range(3):  # register_email limit = 3/hr
        cookie, csrf = bootstrap(client)
        r = client.post(
            "/api/auth/register",
            json={"email": email, "password": "Password123"},
            headers=auth_headers(csrf),
            cookies={"ecommerce_session": cookie},
        )
        assert r.status_code in (201, 409), r.text
    # 4th attempt exhausts the email gate -> 429.
    cookie, csrf = bootstrap(client)
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "Password123"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 429, r.text
    assert r.headers.get("Retry-After")
    assert r.json()["error"]["code"] == "rate_limit_exceeded"
    # No rejected-request side effect: no duplicate user created.
    assert db.query(models.User).filter(models.User.email == email).count() == 1
