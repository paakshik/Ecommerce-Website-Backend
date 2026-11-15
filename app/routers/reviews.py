"""Product reviews router: one review per user/product."""

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Path, Request, status
from sqlalchemy.orm import Session

from app import auth as auth_mod, models
from app.database import get_db
from app.dependencies import csrf_valid, current_user
from app.schemas import ReviewCreate, ReviewOut, ReviewUpdate

router = APIRouter()


@router.post("/products/{product_id}/reviews", status_code=status.HTTP_201_CREATED, response_model=ReviewOut)
def create_review(
    payload: ReviewCreate,
    request: Request,
    product_id: int = Path(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    csrf_valid(request, db, session_cookie, x_csrf_token)
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Product not found"}},
        )
    existing = (
        db.query(models.Review)
        .filter(models.Review.user_id == user.id, models.Review.product_id == product_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "review_exists", "message": "You already reviewed this product"}},
        )
    row = models.Review(user_id=user.id, product_id=product_id, rating=payload.rating, body=payload.body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return ReviewOut.model_validate(row)


@router.put("/products/{product_id}/reviews", response_model=ReviewOut)
def update_review(
    payload: ReviewUpdate,
    request: Request,
    product_id: int = Path(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    csrf_valid(request, db, session_cookie, x_csrf_token)
    row = (
        db.query(models.Review)
        .filter(models.Review.user_id == user.id, models.Review.product_id == product_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Review not found"}},
        )
    if payload.rating is not None:
        row.rating = payload.rating
    if payload.body is not None:
        row.body = payload.body
    db.commit()
    db.refresh(row)
    return ReviewOut.model_validate(row)


@router.delete("/products/{product_id}/reviews", status_code=status.HTTP_204_NO_CONTENT)
def delete_review(
    request: Request,
    product_id: int = Path(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    csrf_valid(request, db, session_cookie, x_csrf_token)
    db.query(models.Review).filter(
        models.Review.user_id == user.id,
        models.Review.product_id == product_id,
    ).delete(synchronize_session=False)
    db.commit()
    return None
