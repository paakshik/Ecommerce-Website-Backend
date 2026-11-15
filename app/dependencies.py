"""FastAPI dependencies: DB session, current user, CSRF, rate limiting."""

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import auth, models, rate_limits
from app.config import get_settings
from app.database import get_db


def current_user(
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth.SESSION_COOKIE_NAME),
) -> models.User:
    """Resolve the authenticated user from the opaque session cookie.

    Raises 401 if no valid session. Never trusts client-provided user IDs/roles.
    """
    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": {"code": "unauthenticated", "message": "Session required"}})
    session = auth.get_session(db, session_cookie)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": {"code": "unauthenticated", "message": "Invalid or expired session"}})
    user = db.query(models.User).filter(models.User.id == session.user_id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": {"code": "unauthenticated", "message": "User not found"}})
    return user


def current_user_optional(
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth.SESSION_COOKIE_NAME),
) -> Optional[models.User]:
    """Resolve the current user, or None if unauthenticated (for public endpoints)."""
    if not session_cookie:
        return None
    session = auth.get_session(db, session_cookie)
    if session is None or session.user_id is None:
        return None
    return db.query(models.User).filter(models.User.id == session.user_id).first()


def require_admin(user: models.User = Depends(current_user)) -> models.User:
    """Require the current user to be an admin."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": {"code": "forbidden", "message": "Admin required"}})
    return user


def csrf_valid(
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth.SESSION_COOKIE_NAME),
    x_csrf_token: Optional[str] = None,
) -> None:
    """Double-submit CSRF validation for state-changing cookie requests.

    Compares the X-CSRF-Token header with the csrf_token stored in the session,
    plus a trusted-origin check.
    """
    if not session_cookie:
        return
    session = auth.get_session(db, session_cookie)
    if session is None:
        return
    provided = x_csrf_token or request.headers.get("x-csrf-token")
    if not provided or not session.csrf_token or provided != session.csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": {"code": "csrf_invalid", "message": "CSRF token missing or invalid"}})
    _check_trusted_origin(request)


def _check_trusted_origin(request: Request) -> None:
    settings = get_settings()
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return
    app_url = settings.PUBLIC_APP_URL.rstrip("/")
    if not origin.rstrip("/").startswith(app_url):
        if settings.ENV != "development":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": {"code": "origin_not_trusted", "message": "Origin not trusted"}})


def _subject_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


# ---- Rate-limit dependencies ----

def rate_limit_login_ip(request: Request, db: Session = Depends(get_db)) -> None:
    _check_ip(db, request, "login_ip")


def rate_limit_login_email(request: Request, db: Session = Depends(get_db), email: Optional[str] = None) -> None:
    _check_multi(db, request, "login", _subject_email(email) or None)


def rate_limit_register_ip(request: Request, db: Session = Depends(get_db)) -> None:
    _check_ip(db, request, "register_ip")


def rate_limit_register_email(request: Request, db: Session = Depends(get_db), email: Optional[str] = None) -> None:
    _check_multi(db, request, "register", _subject_email(email) or None)


def rate_limit_checkout_user(request: Request, db: Session = Depends(get_db), user=None) -> None:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"error": {"code": "unauthenticated", "message": "Authentication required"}})
    limit, window = rate_limits.QUOTAS["checkout_user"]
    allowed, retry = rate_limits.check_bucket(
        db, "checkout_user", rate_limits._digest(str(user.id)), limit, window
    )
    if not allowed:
        raise rate_limits.rate_limit_response(retry)


def rate_limit_checkout_ip(request: Request, db: Session = Depends(get_db)) -> None:
    _check_ip(db, request, "checkout_ip")


def rate_limit_upload_admin(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
) -> None:
    """Require admin plus the per-admin upload quota (enforced before body parse)."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": {"code": "forbidden", "message": "Admin required"}})
    limit, window = rate_limits.QUOTAS["upload_admin"]
    allowed, retry = rate_limits.check_bucket(
        db, "upload_admin", rate_limits._digest(str(user.id)), limit, window
    )
    if not allowed:
        raise rate_limits.rate_limit_response(retry)


def rate_limit_upload_ip(request: Request, db: Session = Depends(get_db)) -> None:
    _check_ip(db, request, "upload_ip")


# ---- internal helpers ----

def _check_ip(db: Session, request: Request, scope: str) -> None:
    ip = rate_limits._parse_client_ip(request)
    if ip is None:
        return
    limit, window = rate_limits.QUOTAS[scope]
    allowed, retry = rate_limits.check_bucket(db, scope, rate_limits._digest(ip), limit, window)
    if not allowed:
        raise rate_limits.rate_limit_response(retry)


def _check_multi(db: Session, request: Request, kind: str, email: Optional[str]) -> None:
    ip = rate_limits._parse_client_ip(request)
    checks = []
    if ip:
        checks.append(("login_ip" if kind == "login" else "register_ip", rate_limits._digest(ip), *rate_limits.QUOTAS["login_ip" if kind == "login" else "register_ip"]))
    if email:
        scope = "login_email" if kind == "login" else "register_email"
        checks.append((scope, rate_limits._digest(email), *rate_limits.QUOTAS[scope]))
    if not checks:
        return
    allowed, retry = rate_limits.check_multi(db, checks)
    if not allowed:
        raise rate_limits.rate_limit_response(retry)
