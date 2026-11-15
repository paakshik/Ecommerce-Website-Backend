"""Public catalog tests: categories and products."""

from fastapi.testclient import TestClient


def test_categories(client: TestClient):
    r = client.get("/api/categories")
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_products_empty(client: TestClient):
    r = client.get("/api/products")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["page"] == 1
    assert body["total"] == 0


def test_product_not_found(client: TestClient):
    r = client.get("/api/products/99999")
    assert r.status_code == 404, r.text
