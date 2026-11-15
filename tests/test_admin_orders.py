"""Admin order listing, fulfillment, and digital-asset attachment tests."""

from app import models
from app.services import razorpay as razorpay_mod
from tests._helpers import login, auth_headers, seed_variant


def _make_admin(client, db):
    cookie, csrf = login(client, "admin@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "admin@example.com").first()
    user.role = "admin"
    db.commit()
    return cookie, csrf


def _multipart_body(fields, boundary="----WebKitFormBoundaryxyz"):
    parts = []
    for name, value, filename, ctype in fields:
        parts.append(b"--" + boundary.encode())
        if filename:
            parts.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
            parts.append(value)
        else:
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(value if isinstance(value, bytes) else value.encode())
        parts.append(b"\r\n")
    parts.append(b"--" + boundary.encode() + b"--\r\n")
    return b"".join(parts)


def _create_paid_order(client, cookie, csrf, variant_id, idem_key, monkeypatch, amount=9999):
    """Checkout then verify payment so the order is 'paid'."""
    created = {}

    def fake_create_order(*a, **k):
        created["id"] = f"order_rzp_{idem_key}"
        return {"id": created["id"], "amount": amount, "currency": "USD", "status": "created"}

    monkeypatch.setattr(razorpay_mod, "create_order", fake_create_order)
    _add = client.post(
        "/api/cart",
        json={"variant_id": variant_id, "quantity": 1},
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert _add.status_code == 200, _add.text
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(csrf), "Idempotency-Key": idem_key},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["order_id"]
    monkeypatch.setattr(razorpay_mod, "verify_payment_signature", lambda *a, **k: True)
    monkeypatch.setattr(
        razorpay_mod,
        "fetch_payment",
        lambda pid: {"id": "pay_x", "order_id": created["id"], "amount": amount, "currency": "USD", "status": "captured"},
    )
    r = client.post(
        f"/api/orders/{order_id}/verify-payment",
        json={
            "razorpay_payment_id": "pay_x",
            "razorpay_order_id": created["id"],
            "razorpay_signature": "sig",
        },
        headers=auth_headers(csrf),
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    return order_id


def test_admin_list_orders_sees_all_orders(client, db, monkeypatch):
    admin_cookie, admin_csrf = _make_admin(client, db)
    buyer_cookie, buyer_csrf = login(client, "buyer@example.com", "Password123")

    variant_id, _ = seed_variant(db, sku="LIST-1")
    buyer_order = _create_paid_order(client, buyer_cookie, buyer_csrf, variant_id, "idem-buyer", monkeypatch)
    admin_order = _create_paid_order(client, admin_cookie, admin_csrf, variant_id, "idem-admin", monkeypatch)

    r = client.get(
        "/api/admin/orders",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 2
    ids = {o["id"] for o in body["items"]}
    assert buyer_order in ids
    assert admin_order in ids
    # Newest first: check ordering by created_at descending.
    dates = [o["created_at"] for o in body["items"] if o["id"] in (buyer_order, admin_order)]
    assert dates == sorted(dates, reverse=True)
    # Each item carries the admin order fields.
    for o in body["items"]:
        assert set(["id", "status", "email", "subtotal_minor", "shipping_minor", "total_minor", "currency", "created_at", "items", "payment_status", "downloadable_item_ids"]).issubset(o.keys())

    # A customer cannot access the admin list.
    r = client.get(
        "/api/admin/orders",
        headers=auth_headers(buyer_csrf),
        cookies={"ecommerce_session": buyer_cookie},
    )
    assert r.status_code == 403, r.text


def test_admin_list_orders_pagination(client, db, monkeypatch):
    admin_cookie, admin_csrf = _make_admin(client, db)
    variant_id, _ = seed_variant(db, sku="PAG-1")
    _create_paid_order(client, admin_cookie, admin_csrf, variant_id, "idem-pag1", monkeypatch)
    _create_paid_order(client, admin_cookie, admin_csrf, variant_id, "idem-pag2", monkeypatch)

    r = client.get(
        "/api/admin/orders?limit=1&offset=0",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= 2
    assert len(r.json()["items"]) == 1


def test_admin_fulfill_paid_order(client, db, monkeypatch):
    admin_cookie, admin_csrf = _make_admin(client, db)
    variant_id, _ = seed_variant(db, sku="FUL-1")
    order_id = _create_paid_order(client, admin_cookie, admin_csrf, variant_id, "idem-ful", monkeypatch)

    r = client.post(
        f"/api/admin/orders/{order_id}/fulfill",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "fulfilled"
    order = db.query(models.Order).get(order_id)
    assert order.status == "fulfilled"


def test_admin_fulfill_pending_order_409(client, db, monkeypatch):
    admin_cookie, admin_csrf = _make_admin(client, db)
    variant_id, _ = seed_variant(db, sku="PEN-1")
    # Order created (pending) but payment never verified.
    monkeypatch.setattr(
        razorpay_mod,
        "create_order",
        lambda *a, **k: {"id": "order_rzp", "amount": 9999, "currency": "USD", "status": "created"},
    )
    _add = client.post(
        "/api/cart",
        json={"variant_id": variant_id, "quantity": 1},
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert _add.status_code == 200, _add.text
    r = client.post(
        "/api/orders",
        json={"shipping_address": None},
        headers={**auth_headers(admin_csrf), "Idempotency-Key": "idem-pending"},
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 201, r.text
    order_id = r.json()["order_id"]

    r = client.post(
        f"/api/admin/orders/{order_id}/fulfill",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 409, r.text


def test_admin_fulfill_already_fulfilled_409(client, db, monkeypatch):
    admin_cookie, admin_csrf = _make_admin(client, db)
    variant_id, _ = seed_variant(db, sku="ALF-1")
    order_id = _create_paid_order(client, admin_cookie, admin_csrf, variant_id, "idem-alf", monkeypatch)

    r1 = client.post(
        f"/api/admin/orders/{order_id}/fulfill",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r1.status_code == 200, r1.text

    r2 = client.post(
        f"/api/admin/orders/{order_id}/fulfill",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "already_fulfilled"


def test_admin_fulfill_not_found_404(client, db):
    admin_cookie, admin_csrf = _make_admin(client, db)
    r = client.post(
        "/api/admin/orders/999999/fulfill",
        headers=auth_headers(admin_csrf),
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 404, r.text


def test_admin_attach_digital_asset(client, db):
    admin_cookie, admin_csrf = _make_admin(client, db)
    variant_id, prod_id = seed_variant(db, sku="DA-1")

    body = _multipart_body([("file", b"digital-contents", "ebook.bin", "application/octet-stream")])
    r = client.post(
        f"/api/admin/products/{prod_id}/digital-assets",
        content=body,
        headers={
            **auth_headers(admin_csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["product_id"] == prod_id
    assert data["content_type"] == "application/octet-stream"
    assert data["byte_size"] == len(b"digital-contents")
    assert data["storage_key"].startswith("digital/")
    ma = db.query(models.MediaAsset).filter(models.MediaAsset.storage_key == data["storage_key"]).first()
    assert ma is not None
    assert ma.product_id == prod_id
    assert ma.uploader_id == _admin_user_id(db)


def _admin_user_id(db):
    return db.query(models.User).filter(models.User.email == "admin@example.com").first().id


def test_admin_attach_digital_asset_product_not_found_404(client, db):
    admin_cookie, admin_csrf = _make_admin(client, db)
    body = _multipart_body([("file", b"x", "x.bin", "application/octet-stream")])
    r = client.post(
        "/api/admin/products/999999/digital-assets",
        content=body,
        headers={
            **auth_headers(admin_csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 404, r.text


def test_admin_attach_digital_asset_empty_file_422(client, db):
    admin_cookie, admin_csrf = _make_admin(client, db)
    variant_id, prod_id = seed_variant(db, sku="DAE-1")
    body = _multipart_body([("file", b"", "empty.bin", "application/octet-stream")])
    r = client.post(
        f"/api/admin/products/{prod_id}/digital-assets",
        content=body,
        headers={
            **auth_headers(admin_csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": admin_cookie},
    )
    assert r.status_code == 422, r.text


def test_admin_endpoints_require_admin_403(client, db):
    buyer_cookie, buyer_csrf = login(client, "buyer@example.com", "Password123")
    variant_id, prod_id = seed_variant(db, sku="NA-1")

    # GET list.
    r = client.get(
        "/api/admin/orders",
        headers=auth_headers(buyer_csrf),
        cookies={"ecommerce_session": buyer_cookie},
    )
    assert r.status_code == 403, r.text

    # POST fulfill.
    r = client.post(
        "/api/admin/orders/1/fulfill",
        headers=auth_headers(buyer_csrf),
        cookies={"ecommerce_session": buyer_cookie},
    )
    assert r.status_code == 403, r.text

    # POST digital asset.
    body = _multipart_body([("file", b"x", "x.bin", "application/octet-stream")])
    r = client.post(
        f"/api/admin/products/{prod_id}/digital-assets",
        content=body,
        headers={
            **auth_headers(buyer_csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": buyer_cookie},
    )
    assert r.status_code == 403, r.text
