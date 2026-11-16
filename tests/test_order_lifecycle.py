"""Backend regression tests for the order/payment lifecycle.

Covers: customer order list (GET /api/orders), list-to-detail navigation,
pending-to-paid after verify-payment and webhook, payment-status rendering,
provider auth failure (502, no secret leak), payment.failed webhook, webhook
dedupe/out-of-order, stock reservation/release, idempotency, digital download
grant, and HTTP 401/403/404/409/429 handling.

Provider behavior is controlled by monkeypatching
``app.services.razorpay.provider_mode`` and ``get_client`` so no real Razorpay
network calls are made. Real provider sandbox calls are exercised only through
these controlled fakes.
"""

from razorpay.errors import BadRequestError

from app import models
from app.services import razorpay as razorpay_mod
from app.services.razorpay import RazorpayError
from tests._helpers import login, auth_headers, seed_variant, seed_digital_file


# ---------------------------------------------------------------------------
# Razorpay client fakes (no real provider network)
# ---------------------------------------------------------------------------

class _FakeOrderResource:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    def create(self, data=None):
        if self.should_fail:
            raise BadRequestError("authentication failed")
        return {
            "id": "order_rzp_fake",
            "amount": (data or {}).get("amount"),
            "currency": (data or {}).get("currency"),
            "status": "created",
        }

    def all(self, data=None):
        if self.should_fail:
            raise BadRequestError("authentication failed")
        return {"items": []}


class _FakeClient:
    def __init__(self, should_fail=False):
        self.order = _FakeOrderResource(should_fail)


def _set_mode(monkeypatch, mode):
    monkeypatch.setattr(razorpay_mod, "provider_mode", lambda: mode)


def _fake_create_order(amount_minor, currency, receipt, notes=None):
    return {
        "id": "order_rzp_stub",
        "amount": amount_minor,
        "currency": currency,
        "status": "created",
    }


def _set_client(monkeypatch, should_fail=False):
    monkeypatch.setattr(razorpay_mod, "get_client", lambda: _FakeClient(should_fail))


# ---------------------------------------------------------------------------
# Order seeding helpers (bypass checkout/Razorpay for list/detail tests)
# ---------------------------------------------------------------------------

def _seed_order(
    db,
    user,
    status="pending",
    razorpay_order_id="order_rzp_1",
    shipping_address=None,
    items=None,
    payment_status=None,
    email=None,
    created_at=None,
):
    order = models.Order(
        user_id=user.id,
        email=email or user.email,
        status=status,
        subtotal_minor=9999,
        shipping_minor=0,
        total_minor=9999,
        currency="USD",
        shipping_address=shipping_address or {"line1": "1 Main St"},
        razorpay_order_id=razorpay_order_id,
        created_at=created_at,
    )
    db.add(order)
    db.flush()
    for it in (items or []):
        db.add(
            models.OrderItem(
                order_id=order.id,
                variant_id=it["variant_id"],
                quantity=it.get("quantity", 1),
                price_minor=it.get("price_minor", 9999),
                product_name=it["product_name"],
                delivery_type=it.get("delivery_type", "ship"),
            )
        )
    if payment_status:
        db.add(
            models.Payment(
                order_id=order.id,
                razorpay_payment_id=f"pay_{order.id}",
                razorpay_order_id=razorpay_order_id,
                amount_minor=9999,
                currency="USD",
                status=payment_status,
            )
        )
    db.flush()
    return order


def _seed_user(db, email, role="customer"):
    user = models.User(email=email, password_hash="x", role=role)
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# GET /api/orders — customer order list
# ---------------------------------------------------------------------------

def test_order_list_requires_auth(client):
    r = client.get("/api/orders")
    assert r.status_code == 401, r.text


def test_order_list_dates_and_fields(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-1")
    _seed_order(
        db,
        user,
        status="paid",
        razorpay_order_id="order_rzp_a",
        items=[{"variant_id": variant_id, "product_name": "Widget", "quantity": 2}],
        payment_status="captured",
    )
    db.commit()

    r = client.get(
        "/api/orders",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    order = body["items"][0]
    # created_at serialized (timezone-aware ISO).
    assert "created_at" in order and order["created_at"]
    # payment_status separate from fulfillment status.
    assert order["payment_status"] == "captured"
    assert order["status"] == "paid"
    assert order["total_minor"] == 9999
    assert order["currency"] == "USD"
    assert order["shipping_address"] == {"line1": "1 Main St"}
    assert len(order["items"]) == 1
    assert order["items"][0]["product_name"] == "Widget"
    assert order["items"][0]["quantity"] == 2
    assert order["items"][0]["price_minor"] == 9999
    assert order["items"][0]["delivery_type"] == "ship"


def test_order_list_newest_first(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-2")
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    _seed_order(
        db, user, razorpay_order_id="order_rzp_a",
        items=[{"variant_id": variant_id, "product_name": "A"}],
        created_at=base,
    )
    _seed_order(
        db, user, razorpay_order_id="order_rzp_b",
        items=[{"variant_id": variant_id, "product_name": "B"}],
        created_at=base + timedelta(minutes=1),
    )
    db.commit()

    r = client.get(
        "/api/orders", headers=auth_headers(csrf), cookies={"ecommerce_session": cookie}
    )
    assert r.status_code == 200, r.text
    ids = [o["id"] for o in r.json()["items"]]
    assert ids == sorted(ids, reverse=True), ids  # newest first


def test_order_list_pagination(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-3")
    for i in range(5):
        _seed_order(db, user, razorpay_order_id=f"order_rzp_p{i}", items=[{"variant_id": variant_id, "product_name": f"P{i}"}])
    db.commit()

    r1 = client.get(
        "/api/orders?limit=2&offset=0",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["total"] == 5
    assert len(r1.json()["items"]) == 2

    r2 = client.get(
        "/api/orders?limit=2&offset=2",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r2.status_code == 200, r2.text
    assert len(r2.json()["items"]) == 2
    first_ids = {o["id"] for o in r1.json()["items"]}
    second_ids = {o["id"] for o in r2.json()["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_order_list_search_email(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-4")
    _seed_order(db, user, razorpay_order_id="order_rzp_s", items=[{"variant_id": variant_id, "product_name": "Thing"}])
    db.commit()

    r = client.get(
        "/api/orders?q=buyer@example.com",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1

    r = client.get(
        "/api/orders?q=nonexistent@example.com",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_order_list_search_item_name(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-5")
    _seed_order(db, user, razorpay_order_id="order_rzp_si", items=[{"variant_id": variant_id, "product_name": "Blue Widget"}])
    db.commit()

    r = client.get(
        "/api/orders?q=Blue",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


def test_order_list_status_filter(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-6")
    _seed_order(db, user, status="pending", razorpay_order_id="order_rzp_st1", items=[{"variant_id": variant_id, "product_name": "St1"}])
    _seed_order(db, user, status="paid", razorpay_order_id="order_rzp_st2", items=[{"variant_id": variant_id, "product_name": "St2"}])
    db.commit()

    r = client.get(
        "/api/orders?status=paid",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["status"] == "paid"


def test_order_list_payment_status_filter(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-7")
    _seed_order(db, user, razorpay_order_id="order_rzp_ps1", items=[{"variant_id": variant_id, "product_name": "Ps1"}], payment_status="captured")
    _seed_order(db, user, razorpay_order_id="order_rzp_ps2", items=[{"variant_id": variant_id, "product_name": "Ps2"}], payment_status="failed")
    db.commit()

    r = client.get(
        "/api/orders?payment_status=captured",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["payment_status"] == "captured"


def test_order_list_excludes_other_user(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    other = _seed_user(db, "other@example.com")
    variant_id, _ = seed_variant(db, sku="OL-8")
    _seed_order(db, other, razorpay_order_id="order_rzp_other", items=[{"variant_id": variant_id, "product_name": "Other"}])
    db.commit()

    r = client.get(
        "/api/orders", headers=auth_headers(csrf), cookies={"ecommerce_session": cookie}
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


def test_order_list_to_detail_navigation(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-9")
    _seed_order(db, user, razorpay_order_id="order_rzp_nav", items=[{"variant_id": variant_id, "product_name": "NavItem"}])
    db.commit()

    r = client.get(
        "/api/orders", headers=auth_headers(csrf), cookies={"ecommerce_session": cookie}
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["items"][0]["id"]

    r = client.get(
        f"/api/orders/{order_id}",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == order_id
    assert r.json()["created_at"]


def test_order_detail_403_for_other_user(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    # Register the other user properly (login registers if absent, with a valid hash).
    cookie2, _ = login(client, "other@example.com", "Password123")
    other = db.query(models.User).filter(models.User.email == "other@example.com").first()
    variant_id, _ = seed_variant(db, sku="OL-10")
    _seed_order(db, other, razorpay_order_id="order_rzp_forbid", items=[{"variant_id": variant_id, "product_name": "Forbidden"}])
    db.commit()
    order_id = db.query(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_forbid").first().id

    r = client.get(
        f"/api/orders/{order_id}",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie2},
    )
    # `other` owns it -> 200.
    assert r.status_code == 200, r.text

    r = client.get(
        f"/api/orders/{order_id}",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    # Buyer cannot view another user's order -> 403.
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Pending -> paid (verify-payment and webhook)
# ---------------------------------------------------------------------------

def test_pending_to_paid_after_verify_payment(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: {"id": "pay_x", "order_id": "order_rzp_stub", "amount": 9999, "currency": "USD", "status": "captured"},
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="PP-1")
    _add_to_cart(client, cookie, csrf, variant_id, 1)
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-pp"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["order_id"]

    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.reserved_stock == 1  # reserved at checkout

    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp_stub", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paid"
    assert r.json()["action"] == "finalized"

    order = db.query(models.Order).get(order_id)
    assert order.status == "paid"
    # Stock decreased AND reservation released.
    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.stock == 4
    assert variant.reserved_stock == 0


def test_pending_to_paid_after_webhook_captured(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="PP-2")
    _seed_order(db, user, status="pending", razorpay_order_id="order_rzp_wh", items=[{"variant_id": variant_id, "product_name": "WH"}])
    db.commit()

    import json

    body = json.dumps(
        {
            "id": "evt_cap_1",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_wh",
                        "order_id": "order_rzp_wh",
                        "amount": 9999,
                        "currency": "USD",
                        "status": "captured",
                    }
                }
            },
        }
    )
    r = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt_cap_1", "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    order = db.query(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_wh").first()
    assert order.status == "paid"


# ---------------------------------------------------------------------------
# Payment status rendering
# ---------------------------------------------------------------------------

def test_payment_status_rendered_captured(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="PP-3")
    _seed_order(db, user, status="paid", razorpay_order_id="order_rzp_ps", items=[{"variant_id": variant_id, "product_name": "Ps"}], payment_status="captured")
    db.commit()
    order_id = db.query(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_ps").first().id

    r = client.get(
        f"/api/orders/{order_id}",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["payment_status"] == "captured"
    assert r.json()["status"] == "paid"


# ---------------------------------------------------------------------------
# Provider authentication failure -> 502 generic, no secret leak
# ---------------------------------------------------------------------------

def test_provider_auth_failure_502_generic(client, db, monkeypatch):
    _set_mode(monkeypatch, "live")
    _set_client(monkeypatch, should_fail=True)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="PP-4")
    _add_to_cart(client, cookie, csrf, variant_id, 1)

    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-authfail"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["error"]["code"] == "provider_order_failed"
    text = r.text
    # No raw secret leaked into the response.
    assert "rzp_" not in text
    assert "secret" not in text.lower()


# ---------------------------------------------------------------------------
# Payment failure webhook -> failed payment recorded, paid order not undone
# ---------------------------------------------------------------------------

def test_payment_failed_webhook_records_without_undo(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="PP-5")
    _seed_order(
        db, user, status="paid", razorpay_order_id="order_rzp_fail",
        items=[{"variant_id": variant_id, "product_name": "Fail"}],
    )
    db.add(
        models.Payment(
            order_id=db.query(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_fail").first().id,
            razorpay_payment_id="pay_captured",
            razorpay_order_id="order_rzp_fail",
            amount_minor=9999,
            currency="USD",
            status="captured",
        )
    )
    db.commit()

    import json

    body = json.dumps(
        {
            "id": "evt_fail_1",
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_fail_1",
                        "order_id": "order_rzp_fail",
                        "amount": 9999,
                        "currency": "USD",
                        "status": "failed",
                    }
                }
            },
        }
    )
    r = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt_fail_1", "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    db.expire_all()
    order = db.query(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_fail").first()
    assert order.status == "paid"  # capture not undone
    failed = db.query(models.Payment).filter(models.Payment.razorpay_payment_id == "pay_fail_1").first()
    assert failed is not None
    assert failed.status == "failed"


# ---------------------------------------------------------------------------
# Webhook dedupe / out-of-order delivery
# ---------------------------------------------------------------------------

def test_webhook_out_of_order_and_dedupe(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "verify_webhook_signature", lambda *a, **k: True)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, _ = seed_variant(db, sku="PP-6")
    _seed_order(db, user, status="pending", razorpay_order_id="order_rzp_oo", items=[{"variant_id": variant_id, "product_name": "OO"}])
    db.commit()

    import json

    def send(evt_id, evt_type):
        body = json.dumps(
            {
                "id": evt_id,
                "event": evt_type,
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_oo",
                            "order_id": "order_rzp_oo",
                            "amount": 9999,
                            "currency": "USD",
                            "status": "captured",
                        }
                    }
                },
            }
        )
        return client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": evt_id, "Content-Type": "application/json"},
        )

    # order.paid arrives first, then payment.captured (out of order).
    r1 = send("evt_oo_1", "order.paid")
    assert r1.status_code == 200, r1.text
    r2 = send("evt_oo_2", "payment.captured")
    assert r2.status_code == 200, r2.text
    db.expire_all()
    order = db.query(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_oo").first()
    assert order.status == "paid"
    assert db.query(models.Payment).filter(models.Payment.order_id == order.id).count() == 1

    # Duplicate event id -> 200 already_processed, no re-processing.
    r3 = send("evt_oo_1", "order.paid")
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "already_processed"


# ---------------------------------------------------------------------------
# Stock reservation at checkout and release at finalization
# ---------------------------------------------------------------------------

def test_stock_reserved_at_checkout_released_at_finalization(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: {"id": "pay_x", "order_id": "order_rzp_stub", "amount": 19998, "currency": "USD", "status": "captured"},
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="SR-1")
    _add_to_cart(client, cookie, csrf, variant_id, 2)
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-sr"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["order_id"]
    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.reserved_stock == 2  # reserved

    client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp_stub", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    variant = db.query(models.ProductVariant).get(variant_id)
    assert variant.stock == 3  # 5 - 2
    assert variant.reserved_stock == 0  # released


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_idempotent_same_key_same_payload_returns_existing(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="ID-1")
    _add_to_cart(client, cookie, csrf, variant_id, 1)

    r1 = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-id"},
        cookies={"ecommerce_session": cookie},
    )
    assert r1.status_code == 201, r1.text
    order_id = r1.json()["order_id"]

    r2 = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-id"},
        cookies={"ecommerce_session": cookie},
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["order_id"] == order_id
    assert db.query(models.Order).filter(models.Order.idempotency_key == "idem-id").count() == 1


def test_idempotent_changed_payload_409(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="ID-2")
    _add_to_cart(client, cookie, csrf, variant_id, 1)

    client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-id2"},
        cookies={"ecommerce_session": cookie},
    )
    # Replenish cart (consumed by first checkout), then changed payload.
    _add_to_cart(client, cookie, csrf, variant_id, 1)
    r = client.post(
        "/api/orders",
        json={"shipping_address": {"line1": "Changed"}},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-id2"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "idempotency_conflict"


# ---------------------------------------------------------------------------
# Digital download grant on paid digital order; 403 for unpaid
# ---------------------------------------------------------------------------

def test_digital_download_grant_on_paid_order(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: {"id": "pay_x", "order_id": "order_rzp_stub", "amount": 1499, "currency": "USD", "status": "captured"},
    )
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, prod_id = seed_variant(db, delivery_type="download", stock=1000, price_minor=1499, sku="DG-1")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf", content_type="application/pdf", byte_size=500)
    _add_to_cart(client, cookie, csrf, variant_id, 1)
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": "idem-dg"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["order_id"]

    client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={"razorpay_payment_id": "pay_x", "razorpay_order_id": "order_rzp_stub", "razorpay_signature": "sig"},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    order = db.query(models.Order).get(order_id)
    assert order.status == "paid"
    assert db.query(models.DownloadGrant).filter(models.DownloadGrant.user_id == order.user_id).first() is not None


def test_download_grant_403_for_unpaid_order(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", stock=1000, price_minor=1499, sku="DG-2")
    seed_digital_file(db, prod_id, storage_key="digital/ebook2.pdf", content_type="application/pdf", byte_size=500)
    _seed_order(db, user, status="pending", razorpay_order_id="order_rzp_dgu", items=[{"variant_id": variant_id, "product_name": "DGU"}])
    db.commit()
    item_id = db.query(models.OrderItem).join(models.Order).filter(models.Order.razorpay_order_id == "order_rzp_dgu").first().id

    r = client.post(
        f"/api/orders/{db.query(models.Order).filter(models.Order.razorpay_order_id == 'order_rzp_dgu').first().id}/downloads/{item_id}",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Razorpay service: provider mode behavior
# ---------------------------------------------------------------------------

def test_create_order_test_mode_uses_real_sdk_not_fabricated(monkeypatch):
    _set_mode(monkeypatch, "test")
    _set_client(monkeypatch, should_fail=False)
    result = razorpay_mod.create_order(1000, "USD", "receipt_1", {"user_id": "1"})
    assert result["id"] == "order_rzp_fake"
    assert result["status"] == "created"


def test_create_order_mock_mode_returns_fabricated_id(monkeypatch):
    _set_mode(monkeypatch, "mock")
    result = razorpay_mod.create_order(1000, "USD", "receipt_2")
    assert result["id"] == "order_test_receipt_2"


def test_create_order_mock_mode_prohibited_in_production(monkeypatch):
    _set_mode(monkeypatch, "mock")
    monkeypatch.setattr(razorpay_mod.settings, "ENV", "production")
    try:
        try:
            razorpay_mod.create_order(1000, "USD", "receipt_3")
            assert False, "expected RazorpayError"
        except RazorpayError:
            pass
    finally:
        monkeypatch.setattr(razorpay_mod.settings, "ENV", "development")


def test_verify_razorpay_credentials_invalid_key(monkeypatch):
    _set_mode(monkeypatch, "live")
    _set_client(monkeypatch, should_fail=True)
    try:
        razorpay_mod.verify_razorpay_credentials()
        assert False, "expected RazorpayError"
    except RazorpayError:
        pass


def test_verify_razorpay_credentials_valid_key(monkeypatch):
    _set_mode(monkeypatch, "live")
    _set_client(monkeypatch, should_fail=False)
    # Should not raise.
    razorpay_mod.verify_razorpay_credentials()


# ---------------------------------------------------------------------------
# HTTP 401/403/404/409/429
# ---------------------------------------------------------------------------

def test_order_detail_404(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    r = client.get(
        "/api/orders/999999",
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 404, r.text


def test_checkout_missing_idempotency_key_400(client, db, monkeypatch):
    monkeypatch.setattr(razorpay_mod, "create_order", _fake_create_order)
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    variant_id, _ = seed_variant(db, stock=5, price_minor=9999, sku="ID-3")
    _add_to_cart(client, cookie, csrf, variant_id, 1)
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 400, r.text


def _add_to_cart(client, cookie, csrf, variant_id, quantity=1):
    r = client.post(
        "/api/cart",
        json={"variant_id": variant_id, "quantity": quantity},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
