"""Admin product/variant/order management router (admin role required, CSRF protected)."""

import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app import auth as auth_mod, models
from app.database import get_db
from app.dependencies import csrf_valid, require_admin, rate_limit_upload_admin, rate_limit_upload_ip
from app.routers import orders as orders_mod
from app.routers.uploads import _extract_uploaded_file
from app.schemas import (
    DigitalAssetAttachOut,
    OrderListOut,
    OrderOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    ProductVariantCreate,
    ProductVariantOut,
)
from app.services.storage import MAX_TOTAL_MULTIPART_BYTES, store_bytes

router = APIRouter()


def _secure() -> bool:
    return __import__("app.config", fromlist=["get_settings"]).get_settings().SESSION_COOKIE_SECURE


@router.get("/products", response_model=dict)
def admin_list_products(
    db: Session = Depends(get_db),
    _admin: models.User = Depends(require_admin),
):
    rows = db.query(models.Product).order_by(models.Product.created_at.desc()).all()
    items = [_product_out(db, r) for r in rows]
    return {"items": items, "total": len(items)}


@router.post("/products", status_code=status.HTTP_201_CREATED, response_model=dict)
def admin_create_product(
    response: Response,
    request: Request,  # noqa: ARG001
    payload: ProductCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
):
    csrf_valid(request, db, session_cookie, x_csrf_token)
    if db.query(models.Product).filter(models.Product.slug == payload.slug).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": {"code": "slug_conflict", "message": "Slug already exists"}})
    product = models.Product(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        delivery_type=payload.delivery_type,
        category_id=payload.category_id,
        active=True,
    )
    db.add(product)
    db.flush()
    for v in payload.variants or []:
        db.add(
            models.ProductVariant(
                product_id=product.id,
                sku=v.sku,
                options=v.options,
                price_minor=v.price_minor,
                currency=v.currency,
                stock=v.stock,
                reserved_stock=0,
                active=True,
            )
        )
    db.commit()
    db.refresh(product)
    return _product_out(db, product)


@router.put("/products/{product_id}", response_model=dict)
def admin_update_product(
    product_id: str,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Product not found"}})
    if payload.name is not None:
        product.name = payload.name
    if payload.slug is not None:
        product.slug = payload.slug
    if payload.description is not None:
        product.description = payload.description
    if payload.delivery_type is not None:
        product.delivery_type = payload.delivery_type
    if payload.active is not None:
        product.active = payload.active
    if payload.category_id is not None:
        product.category_id = payload.category_id
    db.commit()
    db.refresh(product)
    return _product_out(db, product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_product(
    product_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Product not found"}})
    product.active = False  # deactivate, preserve order history
    db.commit()
    return None


@router.post("/products/{product_id}/variants", status_code=status.HTTP_201_CREATED, response_model=dict)
def admin_create_variant(
    product_id: str,
    payload: ProductVariantCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Product not found"}})
    if db.query(models.ProductVariant).filter(models.ProductVariant.sku == payload.sku).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": {"code": "sku_conflict", "message": "SKU already exists"}})
    variant = models.ProductVariant(
        product_id=product.id,
        sku=payload.sku,
        options=payload.options,
        price_minor=payload.price_minor,
        currency=payload.currency,
        stock=payload.stock,
        reserved_stock=0,
        active=True,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return ProductVariantOut.model_validate(variant)


@router.put("/products/{product_id}/variants/{variant_id}", response_model=dict)
def admin_update_variant(
    product_id: str,
    variant_id: str,
    payload: ProductVariantCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    variant = (
        db.query(models.ProductVariant)
        .filter(models.ProductVariant.id == variant_id, models.ProductVariant.product_id == product_id)
        .first()
    )
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Variant not found"}})
    variant.sku = payload.sku
    variant.options = payload.options
    variant.price_minor = payload.price_minor
    variant.currency = payload.currency
    variant.stock = payload.stock
    db.commit()
    db.refresh(variant)
    return ProductVariantOut.model_validate(variant)


@router.delete("/products/{product_id}/variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_variant(
    product_id: str,
    variant_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    variant = (
        db.query(models.ProductVariant)
        .filter(models.ProductVariant.id == variant_id, models.ProductVariant.product_id == product_id)
        .first()
    )
    if not variant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Variant not found"}})
    variant.active = False  # deactivate, preserve historical references
    db.commit()
    return None


@router.get("/orders", response_model=OrderListOut)
def admin_list_orders(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List all orders (admin), newest first, with pagination."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    q = db.query(models.Order).order_by(models.Order.created_at.desc())
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    items = [orders_mod._order_out(db, o) for o in rows]
    return OrderListOut(items=items, total=total)


@router.post("/orders/{order_id}/fulfill", response_model=OrderOut)
def admin_fulfill_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
):
    """Mark a paid order as fulfilled (idempotent)."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Order not found"}},
        )
    if order.status == "fulfilled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "already_fulfilled", "message": "Order already fulfilled"}},
        )
    if order.status != "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "not_paid", "message": "Order is not paid"}},
        )
    order.status = "fulfilled"
    db.commit()
    db.refresh(order)
    return orders_mod._order_out(db, order)


@router.post(
    "/products/{product_id}/digital-assets",
    response_model=DigitalAssetAttachOut,
    status_code=status.HTTP_201_CREATED,
)
async def admin_attach_digital_asset(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
    _admin_quota: None = Depends(rate_limit_upload_admin),
    _ip_quota: None = Depends(rate_limit_upload_ip),
):
    """Attach a private digital file to a product as a MediaAsset."""
    csrf_valid(request, db, session_cookie, x_csrf_token)

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Product not found"}},
        )

    # Content-Length pre-check: reject oversize before parsing the body.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_TOTAL_MULTIPART_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={"error": {"code": "request_too_large", "message": "Total multipart body exceeds 6 MiB"}},
                    headers={"Retry-After": "0"},
                )
        except ValueError:
            pass

    body = await request.body()
    if len(body) > MAX_TOTAL_MULTIPART_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": {"code": "request_too_large", "message": "Total multipart body exceeds 6 MiB"}},
            headers={"Retry-After": "0"},
        )

    content_type = request.headers.get("content-type", "")
    _filename, part_ctype, file_bytes = _extract_uploaded_file(body, content_type)

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "empty_file", "message": "File is empty"}},
        )
    if not part_ctype:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "missing_content_type", "message": "File content type is required"}},
        )

    storage_key = f"digital/{secrets.token_urlsafe(24)}"
    store_bytes(storage_key, file_bytes, part_ctype)

    media = models.MediaAsset(
        product_id=product.id,
        storage_key=storage_key,
        content_type=part_ctype,
        byte_size=len(file_bytes),
        uploader_id=user.id,
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    return DigitalAssetAttachOut(
        id=media.id,
        storage_key=media.storage_key,
        content_type=media.content_type,
        byte_size=media.byte_size,
        product_id=media.product_id,
    )


def _product_out(db: Session, product: models.Product) -> dict:
    variants = [
        ProductVariantOut.model_validate(v) for v in sorted(product.variants, key=lambda v: v.sku)
    ]
    return {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "delivery_type": product.delivery_type,
        "active": product.active,
        "price_minor": min((v.price_minor for v in variants), default=0),
        "currency": (variants[0].currency if variants else "USD"),
        "variants": variants,
        "images": [{"url": f"/api/media/{ma.storage_key}"} for ma in product.media_assets],
        "category": {"id": product.category.id, "name": product.category.name, "slug": product.category.slug} if product.category else None,
    }
