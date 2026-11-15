"""Cart router: session-scoped, persisted cart (anonymous or signed-in)."""

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session, joinedload

from app import auth as auth_mod, models
from app.database import get_db
from app.dependencies import csrf_valid
from app.schemas import CartItemIn, CartItemOut, CartOut
from app.config import get_settings

settings = get_settings()

router = APIRouter()


def _ensure_session(
    db: Session, response: Response, session_cookie: str | None
):
    """Resolve the current session, creating an anonymous one when needed."""
    session = None
    if session_cookie:
        session = auth_mod.get_session(db, session_cookie)
    if session is None:
        raw_token = auth_mod.create_session(db, user_id=None)
        db.commit()
        session = auth_mod.get_session(db, raw_token)
        auth_mod.set_session_cookie(
            response, token=raw_token, secure=settings.SESSION_COOKIE_SECURE
        )
    return session


def _get_or_create_cart(db: Session, session: models.Session) -> models.Cart:
    cart = db.query(models.Cart).filter(models.Cart.session_id == session.id).first()
    if cart is None:
        cart = models.Cart(session_id=session.id)
        db.add(cart)
        db.flush()
    return cart


def _cart_total(db: Session, cart: models.Cart) -> CartOut:
    items = []
    subtotal = 0
    all_digital = True
    for ci in cart.items:
        variant = ci.variant
        if variant is None:
            continue
        product = variant.product
        line_total = variant.price_minor * ci.quantity
        subtotal += line_total
        if product.delivery_type != "download":
            all_digital = False
        items.append(
            CartItemOut(
                variant_id=variant.id,
                sku=variant.sku,
                product_name=product.name,
                quantity=ci.quantity,
                price_minor=variant.price_minor,
                currency=variant.currency,
                delivery_type=product.delivery_type,
                line_total_minor=line_total,
            )
        )
    shipping = 0 if all_digital or not items else settings.SHIPPING_FLAT_RATE_MINOR
    return CartOut(
        items=items,
        subtotal_minor=subtotal,
        shipping_minor=shipping,
        total_minor=subtotal + shipping,
        currency=settings.STORE_CURRENCY,
        item_count=sum(i.quantity for i in items),
    )


@router.get("/cart", response_model=CartOut)
def get_cart(
    response: Response,
    request: Request,  # noqa: ARG001
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
):
    """Return the current session cart with backend-recalculated totals."""
    session = _ensure_session(db, response, session_cookie)
    cart = _get_or_create_cart(db, session)
    db.commit()
    return _cart_total(db, cart)


@router.post("/cart", status_code=status.HTTP_200_OK, response_model=CartOut)
def add_to_cart(
    response: Response,
    request: Request,
    payload: CartItemIn,
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    """Add a variant to the cart (anonymous or signed-in session)."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    session = _ensure_session(db, response, session_cookie)
    variant = (
        db.query(models.ProductVariant)
        .filter(models.ProductVariant.id == payload.variant_id, models.ProductVariant.active == True)  # noqa: E712
        .first()
    )
    if variant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Variant not found"}},
        )
    cart = _get_or_create_cart(db, session)
    existing = (
        db.query(models.CartItem)
        .filter(models.CartItem.cart_id == cart.id, models.CartItem.variant_id == payload.variant_id)
        .first()
    )
    if existing:
        existing.quantity += payload.quantity
    else:
        db.add(models.CartItem(cart_id=cart.id, variant_id=payload.variant_id, quantity=payload.quantity))
    db.commit()
    db.refresh(cart)
    return _cart_total(db, cart)


@router.put("/cart/{variant_id}", response_model=CartOut)
def update_cart_item(
    variant_id: int,
    response: Response,
    request: Request,
    payload: CartItemIn,
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    """Change the quantity of a cart item."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    session = _ensure_session(db, response, session_cookie)
    cart = _get_or_create_cart(db, session)
    item = (
        db.query(models.CartItem)
        .filter(models.CartItem.cart_id == cart.id, models.CartItem.variant_id == variant_id)
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "not_found", "message": "Cart item not found"}},
        )
    item.quantity = payload.quantity
    db.commit()
    db.refresh(cart)
    return _cart_total(db, cart)


@router.delete("/cart/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_cart_item(
    variant_id: int,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: str | None = Header(default=None),
):
    """Remove a variant from the cart."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    session = _ensure_session(db, response, session_cookie)
    cart = _get_or_create_cart(db, session)
    db.query(models.CartItem).filter(
        models.CartItem.cart_id == cart.id, models.CartItem.variant_id == variant_id
    ).delete(synchronize_session=False)
    db.commit()
    return None
