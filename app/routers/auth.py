"""Auth router: session bootstrap, register, login, logout, me.

CSRF double-submit required on register/login/logout. Rate limits per Section 10:
IP gate first (dependency), then email gate (in-route after email extracted).
The IP bucket is consumed once and never charged twice for one request.
"""

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app import auth as auth_mod, models, rate_limits
from app.database import get_db
from app.dependencies import csrf_valid, current_user, rate_limit_login_ip, rate_limit_register_ip
from app.schemas import UserLogin, UserOut, UserRegister

router = APIRouter()


def _secure() -> bool:
    return __import__("app.config", fromlist=["get_settings"]).get_settings().SESSION_COOKIE_SECURE


@router.get("/session", response_model=dict)
def auth_session(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
):
    """Bootstrap an anonymous session and return the CSRF token."""
    session = None
    if session_cookie:
        session = auth_mod.get_session(db, session_cookie)
    if session is None:
        raw_token = auth_mod.create_session(db, user_id=None)
        db.commit()
        auth_mod.set_session_cookie(response, token=raw_token, secure=_secure())
        session = auth_mod.get_session(db, raw_token)
    return {"session_id": session.token_hash, "csrf_token": session.csrf_token}


def _email_gate(db: Session, request: Request, email: str, scope: str) -> None:
    """Email gate for login/register (run after IP gate admitted)."""
    digest = rate_limits._digest(email.strip().lower())
    limit, window = rate_limits.QUOTAS[scope]
    allowed, retry = rate_limits.check_bucket(db, scope, digest, limit, window)
    if not allowed:
        raise rate_limits.rate_limit_response(retry)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=dict)
def register(
    response: Response,
    request: Request,
    payload: UserRegister,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
    _ip: None = Depends(rate_limit_register_ip),
):
    """Register a new customer account (public registration always = customer)."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    # Email gate (IP gate already admitted via dependency).
    _email_gate(db, request, payload.email, "register_email")

    email = payload.email.strip().lower()
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": {"code": "email_conflict", "message": "Email already registered"}})

    try:
        password_hash = auth_mod.hash_password(payload.password)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "invalid_password", "message": "Password too long"}})

    user = models.User(email=email, password_hash=password_hash, role="customer")
    db.add(user)
    db.flush()

    # Rotate the existing anonymous session to the user, preserving the cart.
    existing_session = None
    if session_cookie:
        existing_session = auth_mod.get_session(db, session_cookie)
    if existing_session and existing_session.user_id is None:
        raw_token = auth_mod.rotate_session(db, existing_session, user_id=user.id)
    else:
        raw_token = auth_mod.create_session(db, user_id=user.id)
    db.commit()
    auth_mod.set_session_cookie(response, token=raw_token, secure=_secure())

    return {"user": UserOut.model_validate(user)}


@router.post("/login", response_model=dict)
def login(
    response: Response,
    request: Request,
    payload: UserLogin,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
    _ip: None = Depends(rate_limit_login_ip),
):
    """Email/password login; rotates the session on success."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    _email_gate(db, request, payload.email, "login_email")

    email = payload.email.strip().lower()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not user.password_hash or not auth_mod.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": {"code": "invalid_credentials", "message": "Invalid email or password"}})

    existing_session = None
    if session_cookie:
        existing_session = auth_mod.get_session(db, session_cookie)
    if existing_session:
        raw_token = auth_mod.rotate_session(db, existing_session, user_id=user.id)
    else:
        raw_token = auth_mod.create_session(db, user_id=user.id)
    db.commit()
    auth_mod.set_session_cookie(response, token=raw_token, secure=_secure())

    return {"user": UserOut.model_validate(user)}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None),
):
    """Revoke the session and clear the cookie."""
    csrf_valid(request, db, session_cookie, x_csrf_token)
    if session_cookie:
        auth_mod.revoke_session(db, session_cookie)
        db.commit()
    auth_mod.clear_session_cookie(response, secure=_secure())
    return None


@router.get("/me", response_model=dict)
def me(user: models.User = Depends(current_user)):
    return {"user": UserOut.model_validate(user)}
