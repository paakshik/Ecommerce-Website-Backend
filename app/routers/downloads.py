"""Download routers: grant tokens and private file streaming."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Path, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import auth as auth_mod, models
from app.database import get_db
from app.dependencies import csrf_valid, current_user
from app.config import get_settings
from app.schemas import DownloadTokenOut
from app.services.storage import storage_path

settings = get_settings()

router = APIRouter()


def _now():
    return datetime.now(timezone.utc)


@router.post(
    "/orders/{order_id}/downloads/{item_id}",
    response_model=DownloadTokenOut,
    status_code=status.HTTP_201_CREATED,
)
def issue_download_grant(
    order_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    """Issue a 15-minute download grant token (paid/fulfilled order owner only)."""
    csrf_valid(request, db, session_cookie, x_csrf_token)

    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Order not found"}},
        )
    if order.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "forbidden", "message": "Not your order"}},
        )
    if order.status not in ("paid", "fulfilled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "not_paid", "message": "Order is not paid or fulfilled"}},
        )

    item = (
        db.query(models.OrderItem)
        .filter(models.OrderItem.id == item_id, models.OrderItem.order_id == order.id)
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Order item not found"}},
        )
    if item.variant is None or item.variant.product.delivery_type not in ("download", "both"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "not_downloadable", "message": "Item is not a digital download"}},
        )

    da = (
        db.query(models.DigitalAsset)
        .filter(models.DigitalAsset.order_item_id == item.id)
        .first()
    )
    if da is None:
        source = (
            db.query(models.MediaAsset)
            .filter(models.MediaAsset.product_id == item.variant.product_id)
            .order_by(models.MediaAsset.created_at.desc())
            .first()
        )
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "no_digital_asset", "message": "No digital asset available"}},
            )
        da = models.DigitalAsset(
            order_item_id=item.id,
            file_key=source.storage_key,
            content_type=source.content_type,
            byte_size=source.byte_size,
        )
        db.add(da)
        db.flush()

    # Revoke prior unexpired grants for this asset+user, issue a fresh token.
    db.query(models.DownloadGrant).filter(
        models.DownloadGrant.digital_asset_id == da.id,
        models.DownloadGrant.user_id == user.id,
        models.DownloadGrant.revoked_at.is_(None),
        models.DownloadGrant.expires_at > _now(),
    ).update({models.DownloadGrant.revoked_at: _now()}, synchronize_session=False)

    import secrets

    from app.auth import hash_token

    raw_token = secrets.token_urlsafe(24)
    grant = models.DownloadGrant(
        digital_asset_id=da.id,
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=_now() + timedelta(seconds=settings.DOWNLOAD_LINK_TTL_SECONDS),
    )
    db.add(grant)
    db.commit()
    return DownloadTokenOut(token=raw_token, expires_at=grant.expires_at)


@router.get("/downloads/{token}")
def download_file(
    token: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
):
    """Stream a private file. 403 if expired/revoked/not owned."""
    from app.auth import hash_token

    token_hash = hash_token(token)
    grant = (
        db.query(models.DownloadGrant)
        .filter(models.DownloadGrant.token_hash == token_hash)
        .filter(models.DownloadGrant.revoked_at.is_(None))
        .filter(models.DownloadGrant.expires_at > _now())
        .first()
    )
    if grant is None:
        any_grant = (
            db.query(models.DownloadGrant)
            .filter(models.DownloadGrant.token_hash == token_hash)
            .first()
        )
        if any_grant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "not_found", "message": "Download token not found"}},
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "forbidden", "message": "Download token expired, revoked, or not yours"}},
        )
    if grant.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "forbidden", "message": "Download token not yours"}},
        )

    da = db.query(models.DigitalAsset).filter(models.DigitalAsset.id == grant.digital_asset_id).first()
    if da is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Digital asset not found"}},
        )
    return FileResponse(
        storage_path(da.file_key),
        media_type=da.content_type,
        filename=da.file_key.split("/")[-1],
    )
