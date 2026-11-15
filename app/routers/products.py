"""Public catalog router: products, variants, images, reviews, categories."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload, subqueryload

from app import models
from app.database import get_db
from app.dependencies import current_user_optional
from app.schemas import CategoryOut, ProductList, ProductOut, ProductVariantOut

router = APIRouter()


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    rows = db.query(models.Category).order_by(models.Category.name.asc()).all()
    return [CategoryOut.model_validate(c) for c in rows]


@router.get("/products", response_model=ProductList)
def list_products(
    db: Session = Depends(get_db),
    category: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = db.query(models.Product).filter(models.Product.active == True)  # noqa: E712
    if category:
        q = q.join(models.Category).filter(models.Category.slug == category)
    if search:
        like = f"%{search}%"
        q = q.filter(models.Product.name.ilike(like) | models.Product.slug.ilike(like))
    total = q.count()
    rows = q.order_by(models.Product.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [_product_out(db, r) for r in rows]
    return ProductList(items=items, page=page, total=total)


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: str, db: Session = Depends(get_db)):
    try:
        pid = int(product_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Product not found"}})
    row = (
        db.query(models.Product)
        .options(joinedload(models.Product.variants))
        .filter(models.Product.id == pid)
        .first()
    )
    if not row or not row.active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": {"code": "not_found", "message": "Product not found"}})
    return _product_out(db, row)


def _product_out(db: Session, product: models.Product) -> ProductOut:
    variants = [
        ProductVariantOut.model_validate(v) for v in sorted(product.variants, key=lambda v: v.sku)
    ]
    images = [
        {"url": f"/api/media/{ma.storage_key}"}
        for ma in product.media_assets
    ]
    return ProductOut(
        id=product.id,
        name=product.name,
        slug=product.slug,
        description=product.description,
        delivery_type=product.delivery_type,
        active=product.active,
        price_minor=(min((v.price_minor for v in variants), default=0)),
        currency=(variants[0].currency if variants else "USD"),
        variants=variants,
        images=images,
        category=CategoryOut.model_validate(product.category) if product.category else None,
    )
