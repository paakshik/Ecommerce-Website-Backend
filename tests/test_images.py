"""Image upload tests: admin auth, quotas, validation, storage metadata."""

from app import models
from app.services.storage import ValidationError
from tests._helpers import login, auth_headers


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


def test_image_upload_valid_201(client, db, monkeypatch):
    cookie, csrf = login(client, "admin@example.com", "Password123")
    # Make the user an admin.
    user = db.query(models.User).filter(models.User.email == "admin@example.com").first()
    user.role = "admin"
    db.commit()

    def fake_validate(file_bytes, content_type):
        return {
            "storage_key": "img/abc123.png",
            "content_type": "image/png",
            "byte_size": len(file_bytes),
            "width": 10,
            "height": 10,
        }

    monkeypatch.setattr("app.routers.uploads.validate_and_store_image", fake_validate)

    body = _multipart_body([("file", b"\x89PNG\r\n\x1a\nfake", "test.png", "image/png")])
    r = client.post(
        "/api/admin/uploads/images",
        content=body,
        headers={
            **auth_headers(csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 201, r.text
    body_json = r.json()
    assert body_json["storage_key"] == "img/abc123.png"
    # Metadata stored.
    ma = db.query(models.MediaAsset).filter(models.MediaAsset.storage_key == "img/abc123.png").first()
    assert ma is not None
    assert ma.uploader_id == user.id


def test_image_upload_invalid_422(client, db, monkeypatch):
    cookie, csrf = login(client, "admin@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "admin@example.com").first()
    user.role = "admin"
    db.commit()

    def fake_validate_invalid(file_bytes, content_type):
        raise ValidationError("not_a_valid_image")

    monkeypatch.setattr("app.routers.uploads.validate_and_store_image", fake_validate_invalid)

    body = _multipart_body([("file", b"not an image", "test.svg", "image/svg+xml")])
    r = client.post(
        "/api/admin/uploads/images",
        content=body,
        headers={
            **auth_headers(csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 422, r.text


def test_image_upload_requires_admin_403(client, db):
    cookie, csrf = login(client, "buyer@example.com", "Password123")
    body = _multipart_body([("file", b"x", "test.png", "image/png")])
    r = client.post(
        "/api/admin/uploads/images",
        content=body,
        headers={
            **auth_headers(csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 403, r.text


def test_image_upload_missing_csrf_403(client, db):
    cookie, csrf = login(client, "admin@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "admin@example.com").first()
    user.role = "admin"
    db.commit()
    body = _multipart_body([("file", b"x", "test.png", "image/png")])
    r = client.post(
        "/api/admin/uploads/images",
        content=body,
        headers={
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 403, r.text


def test_image_upload_oversize_413(client, db):
    cookie, csrf = login(client, "admin@example.com", "Password123")
    user = db.query(models.User).filter(models.User.email == "admin@example.com").first()
    user.role = "admin"
    db.commit()
    # Body larger than 6 MiB total multipart limit.
    big = b"\x00" * (7 * 1024 * 1024)
    body = _multipart_body([("file", big, "test.png", "image/png")])
    r = client.post(
        "/api/admin/uploads/images",
        content=body,
        headers={
            **auth_headers(csrf),
            "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryxyz",
        },
        cookies={"ecommerce_session": cookie},
    )
    assert r.status_code == 413, r.text
