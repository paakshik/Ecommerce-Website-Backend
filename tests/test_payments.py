"""Payments finalization service tests: payment_review, idempotency, failed tracking."""

from app import models
from app.services import payments


def _setup(db):
    user = models.User(email="b@example.com", password_hash="x", role="customer")
    db.add(user)
    db.flush()
    return user


def _make_order(db, user, status="pending", total=9999, rzp_order_id="order_rzp"):
    order = models.Order(
        user_id=user.id, email=user.email, status=status,
        subtotal_minor=total, shipping_minor=0, total_minor=total,
        currency="USD", razorpay_order_id=rzp_order_id,
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


def test_finalize_pending_to_paid_releases_stock(client, db):
    from app.services import razorpay as _rz  # noqa
    user = _setup(db)
    cat = models.Category(name="C", slug="c"); db.add(cat); db.flush()
    prod = models.Product(name="P", slug="p", delivery_type="ship", category_id=cat.id, active=True); db.add(prod); db.flush()
    order = _make_order(db, user, total=2000)
    v = _make_variant(db, prod, stock=10, reserved=2, sku="S-1")
    db.add(models.OrderItem(order_id=order.id, variant_id=v.id, quantity=2, price_minor=1000,
                            product_name="P", delivery_type="ship"))
    db.flush()

    order, payment, action, token = payments.finalize_payment(
        db, order, "pay_1", 2000, "USD", "captured"
    )
    assert action == "finalized"
    assert order.status == "paid"
    # stock 10-2=8, reserved 2-2=0.
    assert v.stock == 8
    assert v.reserved_stock == 0
    assert payment.razorpay_payment_id == "pay_1"


def test_finalize_already_paid_idempotent(client, db):
    user = _setup(db)
    order = _make_order(db, user, status="paid")
    order, payment, action, token = payments.finalize_payment(db, order, "pay_1", 2000, "USD", "captured")
    assert action == "already_paid"
    assert order.status == "paid"


def test_finalize_insufficient_stock_payment_review(client, db):
    user = _setup(db)
    cat = models.Category(name="C", slug="c"); db.add(cat); db.flush()
    prod = models.Product(name="P", slug="p", delivery_type="ship", category_id=cat.id, active=True); db.add(prod); db.flush()
    order = _make_order(db, user, total=2000)
    # Only 1 in stock but 2 reserved already; 2 needed.
    v = _make_variant(db, prod, stock=1, reserved=0, sku="S-2")
    db.add(models.OrderItem(order_id=order.id, variant_id=v.id, quantity=2, price_minor=1000,
                            product_name="P", delivery_type="ship"))
    db.flush()

    order, payment, action, token = payments.finalize_payment(
        db, order, "pay_2", 2000, "USD", "captured"
    )
    assert action == "payment_review"
    assert order.status == "payment_review"
    # Stock untouched (not finalized).
    assert v.stock == 1
    assert v.reserved_stock == 0


def test_finalize_digital_only_no_stock_gating(client, db):
    user = _setup(db)
    cat = models.Category(name="C", slug="c"); db.add(cat); db.flush()
    prod = models.Product(name="P", slug="p", delivery_type="download", category_id=cat.id, active=True); db.add(prod); db.flush()
    order = _make_order(db, user, total=1499)
    v = _make_variant(db, prod, stock=0, reserved=0, sku="S-3", delivery_type="download")
    db.add(models.OrderItem(order_id=order.id, variant_id=v.id, quantity=5, price_minor=1499,
                            product_name="P", delivery_type="download"))
    db.flush()

    order, payment, action, token = payments.finalize_payment(
        db, order, "pay_3", 1499, "USD", "captured"
    )
    assert action == "finalized"
    assert order.status == "paid"
    # Digital: stock untouched.
    assert v.stock == 0


def test_payment_failed_without_undo(client, db):
    # A failed payment row can exist alongside a paid order without undoing it.
    user = _setup(db)
    order = _make_order(db, user, status="paid")
    db.add(models.Payment(
        order_id=order.id, razorpay_payment_id="pay_fail",
        razorpay_order_id=order.razorpay_order_id, amount_minor=9999,
        currency="USD", status="failed",
    ))
    db.flush()
    assert db.query(models.Payment).filter(models.Payment.razorpay_payment_id == "pay_fail").first().status == "failed"
    assert order.status == "paid"  # capture not undone
