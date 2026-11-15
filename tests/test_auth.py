"""Auth flow tests: session, register, login, logout, me, CSRF."""

from fastapi.testclient import TestClient


def _bootstrap(client: TestClient):
    r = client.get("/api/auth/session")
    assert r.status_code == 200, r.text
    return r.cookies.get("ecommerce_session"), r.json()["csrf_token"]


def _refresh(client: TestClient, cookie: str):
    """Re-read the session to get the current CSRF token after rotation."""
    r = client.get("/api/auth/session", cookies={"ecommerce_session": cookie})
    assert r.status_code == 200, r.text
    return r.cookies.get("ecommerce_session") or cookie, r.json()["csrf_token"]


def _auth(client, email, password, csrf, cookie, path="/api/auth/login"):
    return client.post(
        path,
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )


def test_register_login_me_logout(client: TestClient):
    cookie, csrf = _bootstrap(client)
    email = "auth-user@example.com"

    # Register (rotates the anonymous session to the user -> new cookie/csrf).
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "Password123"},
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["email"] == email
    assert r.json()["user"]["role"] == "customer"
    cookie, csrf = _refresh(client, r.cookies.get("ecommerce_session") or cookie)

    # Duplicate email -> 409.
    r = _auth(client, email, "Password123", csrf, cookie, path="/api/auth/register")
    assert r.status_code == 409, r.text

    # Missing CSRF -> 403.
    r = client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "Password123"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 403, r.text

    # Me.
    r = client.get("/api/auth/me", cookies={"ecommerce_session": cookie})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == email

    # Login (rotates session -> new cookie/csrf).
    r = _auth(client, email, "Password123", csrf, cookie)
    assert r.status_code == 200, r.text
    cookie, csrf = _refresh(client, r.cookies.get("ecommerce_session") or cookie)

    # Wrong password -> 401.
    r = _auth(client, email, "bad", csrf, cookie)
    assert r.status_code == 401, r.text

    # Missing CSRF on login -> 403.
    r = client.post(
        "/api/auth/login",
        json={"email": email, "password": "Password123"},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 403, r.text

    # Logout.
    r = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 204, r.text

    # Me after logout -> 401.
    r = client.get("/api/auth/me", cookies={"ecommerce_session": cookie})
    assert r.status_code == 401, r.text


def test_password_too_long(client: TestClient):
    cookie, csrf = _bootstrap(client)
    long_pw = "x" * 100
    r = client.post(
        "/api/auth/register",
        json={"email": "longpw@example.com", "password": long_pw},
        headers={"X-CSRF-Token": csrf},
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 400, r.text
