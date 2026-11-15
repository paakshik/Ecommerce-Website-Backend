"""Image upload router: admin only, quotas enforced before multipart parsing."""

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app import auth as auth_mod, models
from app.database import get_db
from app.dependencies import csrf_valid, rate_limit_upload_admin, rate_limit_upload_ip, require_admin
from app.config import get_settings
from app.schemas import ImageUploadOut
from app.services.storage import MAX_TOTAL_MULTIPART_BYTES, ValidationError, validate_and_store_image

settings = get_settings()

router = APIRouter()

_MAX_TOTAL_BYTES = MAX_TOTAL_MULTIPART_BYTES  # 6 MiB


def _extract_uploaded_file(body: bytes, content_type: str):
    """Parse multipart/form-data manually and return (filename, content_type, bytes)
    for the part named 'file'. Enforces the total multipart size via caller."""
    if not content_type or "boundary=" not in content_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "missing_boundary", "message": "Missing multipart boundary"}},
        )
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    if not boundary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "missing_boundary", "message": "Empty multipart boundary"}},
        )
    marker = b"--" + boundary.encode("utf-8")
    parts = body.split(marker)
    for part in parts:
        part = part.strip()
        if not part or part == b"--":
            continue
        # Split headers from body at the first blank line.
        if b"\r\n\r\n" not in part:
            continue
        head, _, body_part = part.partition(b"\r\n\r\n")
        body_part = body_part.rstrip(b"\r\n")
        headers_text = head.decode("utf-8", errors="replace")
        disposition = ""
        part_ctype = "application/octet-stream"
        for line in headers_text.split("\r\n"):
            line = line.strip()
            if line.lower().startswith("content-disposition:"):
                disposition = line[len("content-disposition:"):].strip()
            elif line.lower().startswith("content-type:"):
                part_ctype = line[len("content-type:"):].strip()
        if 'name="file"' not in disposition:
            continue
        import re

        m = re.search(r'filename="([^"]*)"', disposition)
        filename = m.group(1) if m else "upload"
        return filename, part_ctype, body_part
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": {"code": "no_file_part", "message": "No 'file' part in multipart request"}},
    )


@router.post("/admin/uploads/images", response_model=ImageUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_image(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
    _admin_quota: None = Depends(rate_limit_upload_admin),
    _ip_quota: None = Depends(rate_limit_upload_ip),
):
    """Upload a single product image.

    Quotas (and admin auth) are enforced before the multipart body is parsed.
    One JPEG/PNG/WebP <= 5 MiB; total multipart <= 6 MiB.
    """
    csrf_valid(request, db, session_cookie, x_csrf_token)

    # Content-Length pre-check: reject oversize before parsing the body.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={"error": {"code": "request_too_large", "message": "Total multipart body exceeds 6 MiB"}},
                    headers={"Retry-After": "0"},
                )
        except ValueError:
            pass

    body = await request.body()
    if len(body) > _MAX_TOTAL_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": {"code": "request_too_large", "message": "Total multipart body exceeds 6 MiB"}},
            headers={"Retry-After": "0"},
        )

    content_type = request.headers.get("content-type", "")
    try:
        filename, part_ctype, file_bytes = _extract_uploaded_file(body, content_type)
    except HTTPException:
        raise

    try:
        meta = validate_and_store_image(file_bytes, part_ctype)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": str(exc), "message": "Invalid image upload"}},
        )

    media = models.MediaAsset(
        storage_key=meta["storage_key"],
        content_type=meta["content_type"],
        byte_size=meta["byte_size"],
        uploader_id=user.id,
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    return ImageUploadOut(
        storage_key=meta["storage_key"],
        url=f"/api/media/{meta['storage_key']}",
        content_type=meta["content_type"],
        byte_size=meta["byte_size"],
        width=meta["width"],
        height=meta["height"],
    )
