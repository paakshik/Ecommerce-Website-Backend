"""Private file storage for images and digital assets.

Local backend by default. Images are validated, re-encoded and stripped of
metadata before storage. Digital files are stored privately and served only
through expiring, access-controlled download grants.
"""

from datetime import datetime
from io import BytesIO
import os
import secrets
from typing import Optional

from PIL import Image, UnidentifiedImageError

from app.config import get_settings

settings = get_settings()

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_BYTES = settings.UPLOAD_MAX_IMAGE_BYTES  # 5 MiB
MAX_TOTAL_MULTIPART_BYTES = 6 * 1024 * 1024  # 6 MiB
MAX_PIXELS = 20_000_000  # 20 MP
MAX_DIMENSION = 8192


class StorageError(Exception):
    """Raised for storage/backend failures."""


class ValidationError(Exception):
    """Raised for invalid uploads (HTTP 422)."""


def _storage_dir() -> str:
    return settings.STORAGE_LOCAL_DIRECTORY or "./storage"


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _random_key(prefix: str, extension: str) -> str:
    return f"{prefix}/{secrets.token_urlsafe(24)}.{extension.lstrip('.')}"


def validate_and_store_image(
    file_bytes: bytes, content_type: str
) -> dict:
    """Validate and store an uploaded image.

    Enforces: single JPEG/PNG/WebP, <=5 MiB, <=20 MP, <=8192 px per dimension.
    Re-encodes and strips metadata. Returns storage metadata for media_assets.
    Raises ValidationError on invalid uploads.
    """
    if len(file_bytes) > MAX_IMAGE_BYTES:
        raise ValidationError("image_exceeds_size_limit")

    try:
        img = Image.open(BytesIO(file_bytes))
        img.load()
    except UnidentifiedImageError:
        raise ValidationError("not_a_valid_image")
    except Exception:
        raise ValidationError("not_a_valid_image")

    fmt = img.format
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("unsupported_image_format")

    width, height = img.size
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValidationError("image_dimension_exceeds_limit")
    if width * height > MAX_PIXELS:
        raise ValidationError("image_megapixel_exceeds_limit")

    # Re-encode to the same format, stripping metadata.
    buf = BytesIO()
    save_kwargs = {}
    if fmt == "JPEG":
        save_kwargs["quality"] = 85
        save_kwargs["optimize"] = True
        img = img.convert("RGB")
    elif fmt == "PNG":
        save_kwargs["optimize"] = True
    img.save(buf, format=fmt, **save_kwargs)
    out_bytes = buf.getvalue()

    ext = fmt.lower()
    storage_key = _random_key("images", ext)
    _store_bytes(storage_key, out_bytes, content_type or f"image/{ext}")

    return {
        "storage_key": storage_key,
        "content_type": content_type or f"image/{ext}",
        "byte_size": len(out_bytes),
        "width": width,
        "height": height,
    }


def store_bytes(storage_key: str, data: bytes, content_type: str) -> None:
    """Persist raw bytes under a random storage key (private storage)."""
    _store_bytes(storage_key, data, content_type)


def _store_bytes(storage_key: str, data: bytes, content_type: str) -> None:
    directory = _storage_dir()
    _ensure_dir(directory)
    path = os.path.join(directory, storage_key)
    _ensure_dir(os.path.dirname(path))
    with open(path, "wb") as fh:
        fh.write(data)


def retrieve_bytes(storage_key: str) -> bytes:
    """Retrieve stored bytes by key (private storage). Raises FileNotFoundError."""
    path = os.path.join(_storage_dir(), storage_key)
    with open(path, "rb") as fh:
        return fh.read()


def storage_path(storage_key: str) -> str:
    return os.path.join(_storage_dir(), storage_key)
