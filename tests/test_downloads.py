"""Download grant and private-file streaming tests."""

import os
import secrets
from datetime import datetime, timedelta, timezone

from app import models
from app.auth import hash_token
from app.services import storage as storage_mod
from tests._helpers import login, auth_headers, seed_variant, seed_digital_file


def _now():
    return datetime.now(timezone.utc)


def _make_grant(db, user, digital_asset, revoked=False, expired=False, owned=True):
    raw = secrets.token_urlsafe(24)
    grant = models.DownloadGrant(
        digital_asset_id=digital_asset.id,
        user_id=user.id if owned else (user.id + 999),  # different user (may not exist; fk issue!)
        token_hash=hash_token(raw),
        expires_at=_now() - timedelta(seconds=10) if expired else _now() + timedelta(minutes=15),
        revoked_at=_now() if revoked else None,
    )
    # For the not-owned case use a real second user.
    if not owned:
        other = models.User(email=f"other+{user.id}@example.com", password_hash="x", role="customer")
        db.add(other)
        db.flush()
        grant.user_id = other.id
    db.add(grant)
    db.flush()
    return raw, grant


def test_download_success(client, db, monkeypatch, tmp_path):
    # Real private file served from storage.
    file_path = tmp_path / "secret.bin"
    file_path.write_bytes(b"binary-payload")
    monkeypatch.setattr("app.routers.downloads.storage_path", lambda key: str(file_path))

    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", price_minor=1499, sku="DL-1")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf", content_type="application/pdf", byte_size=500)

    order = models.Order(user_id=user.id, email=user.email, status="paid",
                         subtotal_minor=1499, shipping_minor=0, total_minor=1499, currency="USD")
    db.add(order)
    db.flush()
    item = models.OrderItem(order_id=order.id, variant_id=variant_id, quantity=1,
                            price_minor=1499, product_name="E-Book", delivery_type="download")
    db.add(item)
    db.flush()
    da = models.DigitalAsset(order_item_id=item.id, file_key="digital/ebook.pdf",
                             content_type="application/pdf", byte_size=500)
    db.add(da)
    db.flush()
    raw, grant = _make_grant(db, user, da)

    r = client.get(f"/api/downloads/{raw}", cookies={"ecommerce_session": cookie})
    assert r.status_code == 200, r.text
    assert r.content == b"binary-payload"


def test_download_missing_cookie_401(client, db, monkeypatch, tmp_path):
    file_path = tmp_path / "secret.bin"
    file_path.write_bytes(b"x")
    monkeypatch.setattr("app.routers.downloads.storage_path", lambda key: str(file_path))

    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", price_minor=1499, sku="DL-2")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf")
    order = models.Order(user_id=user.id, email=user.email, status="paid",
                         subtotal_minor=1499, shipping_minor=0, total_minor=1499, currency="USD")
    db.add(order); db.flush()
    item = models.OrderItem(order_id=order.id, variant_id=variant_id, quantity=1,
                            price_minor=1499, product_name="E-Book", delivery_type="download")
    db.add(item); db.flush()
    da = models.DigitalAsset(order_item_id=item.id, file_key="digital/ebook.pdf",
                             content_type="application/pdf", byte_size=500)
    db.add(da); db.flush()
    raw, _ = _make_grant(db, user, da)

    # Truly no session cookie: clear the TestClient jar populated by login().
    client.cookies.clear()
    r = client.get(f"/api/downloads/{raw}")  # no cookie
    assert r.status_code == 401, r.text


def test_download_expired_403(client, db, monkeypatch, tmp_path):
    file_path = tmp_path / "secret.bin"; file_path.write_bytes(b"x")
    monkeypatch.setattr("app.routers.downloads.storage_path", lambda key: str(file_path))

    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", price_minor=1499, sku="DL-3")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf")
    order = models.Order(user_id=user.id, email=user.email, status="paid",
                         subtotal_minor=1499, shipping_minor=0, total_minor=1499, currency="USD")
    db.add(order); db.flush()
    item = models.OrderItem(order_id=order.id, variant_id=variant_id, quantity=1,
                            price_minor=1499, product_name="E-Book", delivery_type="download")
    db.add(item); db.flush()
    da = models.DigitalAsset(order_item_id=item.id, file_key="digital/ebook.pdf",
                             content_type="application/pdf", byte_size=500)
    db.add(da); db.flush()
    raw, _ = _make_grant(db, user, da, expired=True)

    r = client.get(f"/api/downloads/{raw}", cookies={"ecommerce_session": cookie})
    assert r.status_code == 403, r.text


def test_download_revoked_403(client, db, monkeypatch, tmp_path):
    file_path = tmp_path / "secret.bin"; file_path.write_bytes(b"x")
    monkeypatch.setattr("app.routers.downloads.storage_path", lambda key: str(file_path))

    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", price_minor=1499, sku="DL-4")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf")
    order = models.Order(user_id=user.id, email=user.email, status="paid",
                         subtotal_minor=1499, shipping_minor=0, total_minor=1499, currency="USD")
    db.add(order); db.flush()
    item = models.OrderItem(order_id=order.id, variant_id=variant_id, quantity=1,
                            price_minor=1499, product_name="E-Book", delivery_type="download")
    db.add(item); db.flush()
    da = models.DigitalAsset(order_item_id=item.id, file_key="digital/ebook.pdf",
                             content_type="application/pdf", byte_size=500)
    db.add(da); db.flush()
    raw, _ = _make_grant(db, user, da, revoked=True)

    r = client.get(f"/api/downloads/{raw}", cookies={"ecommerce_session": cookie})
    assert r.status_code == 403, r.text


def test_download_not_yours_403(client, db, monkeypatch, tmp_path):
    file_path = tmp_path / "secret.bin"; file_path.write_bytes(b"x")
    monkeypatch.setattr("app.routers.downloads.storage_path", lambda key: str(file_path))

    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", price_minor=1499, sku="DL-5")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf")
    order = models.Order(user_id=user.id, email=user.email, status="paid",
                         subtotal_minor=1499, shipping_minor=0, total_minor=1499, currency="USD")
    db.add(order); db.flush()
    item = models.OrderItem(order_id=order.id, variant_id=variant_id, quantity=1,
                            price_minor=1499, product_name="E-Book", delivery_type="download")
    db.add(item); db.flush()
    da = models.DigitalAsset(order_item_id=item.id, file_key="digital/ebook.pdf",
                             content_type="application/pdf", byte_size=500)
    db.add(da); db.flush()
    raw, _ = _make_grant(db, user, da, owned=False)

    r = client.get(f"/api/downloads/{raw}", cookies={"ecommerce_session": cookie})
    assert r.status_code == 403, r.text


def test_issue_download_grant_requires_paid(client, db, monkeypatch):
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "buyer@example.com").first()
    variant_id, prod_id = seed_variant(db, delivery_type="download", price_minor=1499, sku="DL-6")
    seed_digital_file(db, prod_id, storage_key="digital/ebook.pdf")

    # Pending order -> 403.
    order = models.Order(user_id=user.id, email=user.email, status="pending",
                         subtotal_minor=1499, shipping_minor=0, total_minor=1499, currency="USD")
    db.add(order); db.flush()
    item = models.OrderItem(order_id=order.id, variant_id=variant_id, quantity=1,
                            price_minor=1499, product_name="E-Book", delivery_type="download")
    db.add(item); db.flush()

    r = client.post(
        f"/api/orders/{order.id}/downloads/{item.id}",
        headers=auth_headers(csrf), cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 403, r.text
