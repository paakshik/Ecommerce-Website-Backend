"""Wishlist router: GET/POST/DELETE for the current user."""

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Path, Request, status
from sqlalchemy.orm import Session

from app import auth as auth_mod, models
from app.database import get_db
from app.dependencies import csrf_valid, current_user
from app.schemas import WishlistItemIn, WishlistItemOut

router = APIRouter()


@router.get("/wishlist", response_model=list[WishlistItemOut])
def list_wishlist(
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
):
    rows = (
        db.query(models.WishlistItem)
        .filter(models.WishlistItem.user_id == user.id)
        .order_by(models.WishlistItem.created_at.desc())
        .all()
    )
    return [WishlistItemOut.model_validate(r) for r in rows]


@router.post("/wishlist", status_code=status.HTTP_201_CREATED, response_model=WishlistItemOut)
def add_wishlist(
    request: Request,
    payload: WishlistItemIn,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    csrf_valid(request, db, session_cookie, x_csrf_token)
    product = db.query(models.Product).filter(models.Product.id == payload.product_id).first()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Product not found"}},
        )
    existing = (
        db.query(models.WishlistItem)
        .filter(models.WishlistItem.user_id == user.id, models.WishlistItem.product_id == payload.product_id)
        .first()
    )
    if existing:
        return WishlistItemOut.model_validate(existing)
    row = models.WishlistItem(user_id=user.id, product_id=payload.product_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return WishlistItemOut.model_validate(row)


@router.delete("/wishlist/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_wishlist(
    request: Request,
    product_id: int = Path(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    csrf_valid(request, db, session_cookie, x_csrf_token)
    db.query(models.WishlistItem).filter(
        models.WishlistItem.user_id == user.id,
        models.WishlistItem.product_id == product_id,
    ).delete(synchronize_session=False)
    db.commit()
    return None
