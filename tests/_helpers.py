"""Shared helpers for commerce tests."""

from app import models


def _refresh(client, cookie):
    r = client.get("/api/auth/session", cookies={"ecommerce_session": cookie})
    assert r.status_code == 200, r.text
    return r.cookies.get("ecommerce_session") or cookie, r.json()["csrf_token"]


def bootstrap(client):
    r = client.get("/api/auth/session")
    assert r.status_code == 200, r.text
    return r.cookies.get("ecommerce_session"), r.json()["csrf_token"]


def register(client, email, password="Password123"):
    cookie, csrf = bootstrap(client)
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    cookie, csrf = _refresh(client, r.cookies.get("ecommerce_session") or cookie)
    return cookie, csrf


def login(client, email, password):
    cookie, csrf = bootstrap(client)
    # Ensure the account exists (idempotent).
    client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )
    # Re-fetch CSRF after registration rotation.
    cookie, csrf = _refresh(client, cookie)
    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 200, r.text
    cookie, csrf = _refresh(client, r.cookies.get("ecommerce_session") or cookie)
    return cookie, csrf


def auth_headers(csrf):
    return {"X-CSRF-Token": csrf}


def seed_variant(db, delivery_type="ship", stock=10, price_minor=9999, sku="SKU-1", product_slug="p"):
    cat = models.Category(name="C", slug="c")
    db.add(cat)
    db.flush()
    prod = models.Product(
        name="P", slug=product_slug, delivery_type=delivery_type,
        category_id=cat.id, active=True,
    )
    db.add(prod)
    db.flush()
    var = models.ProductVariant(
        product_id=prod.id, sku=sku, options=None,
        price_minor=price_minor, currency="USD",
        stock=stock, reserved_stock=0, active=True,
    )
    db.add(var)
    db.flush()
    return var.id, prod.id


def seed_digital_file(db, product_id, storage_key="digital/file.bin", content_type="application/octet-stream", byte_size=100):
    ma = models.MediaAsset(
        product_id=product_id, storage_key=storage_key,
        content_type=content_type, byte_size=byte_size, uploader_id=None,
    )
    db.add(ma)
    db.flush()
    return ma
