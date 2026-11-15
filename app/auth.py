"""Password authentication and opaque cookie session management.

- passlib + bcrypt; reject UTF-8 passwords over 72 bytes instead of truncating.
- Session: random opaque cookie token; hash stored in sessions table with
  user_id, csrf_token, expires_at. Rotate on login. Revoke on logout.
- Never log credentials or hashes.
"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings

settings = get_settings()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE_NAME = settings.SESSION_COOKIE_NAME
SESSION_MAX_AGE_SECONDS = settings.SESSION_MAX_AGE_SECONDS


def hash_password(password: str) -> str:
    """Hash a password, rejecting inputs over 72 UTF-8 bytes."""
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError("password exceeds 72 UTF-8 bytes")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hash. Rejects over-72-byte inputs."""
    plain_bytes = plain_password.encode("utf-8")
    if len(plain_bytes) > 72:
        return False
    return pwd_context.verify(plain_password, hashed_password)


def generate_token() -> str:
    """Generate a random opaque token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash an opaque token for storage (sha256 hex)."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    *,
    user_id: Optional[int],
    csrf_token: Optional[str] = None,
    max_age_seconds: int = SESSION_MAX_AGE_SECONDS,
) -> str:
    """Create a session row and return the raw opaque token (cookie value).

    The raw token is never stored; only its hash is persisted.
    """
    raw_token = generate_token()
    token_hash = hash_token(raw_token)
    if csrf_token is None:
        csrf_token = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max_age_seconds)

    db_session = models.Session(
        token_hash=token_hash,
        user_id=user_id,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )
    db.add(db_session)
    db.flush()
    return raw_token


def get_session(db: Session, token: str) -> Optional[models.Session]:
    """Fetch a valid (non-expired, non-revoked) session by token hash."""
    token_hash = hash_token(token)
    now = datetime.now(timezone.utc)
    return (
        db.query(models.Session)
        .filter(models.Session.token_hash == token_hash)
        .filter(models.Session.expires_at > now)
        .first()
    )


def rotate_session(db: Session, session: models.Session, *, user_id: Optional[int]) -> str:
    """Rotate the session identifier (new token hash), preserving user_id/cart.

    Returns the new raw token (cookie value).
    """
    raw_token = generate_token()
    session.token_hash = hash_token(raw_token)
    session.user_id = user_id
    session.csrf_token = secrets.token_urlsafe(24)
    session.expires_at = datetime.now(timezone.utc) + timedelta(seconds=SESSION_MAX_AGE_SECONDS)
    db.add(session)
    db.flush()
    return raw_token


def revoke_session(db: Session, token: str) -> None:
    """Revoke a session by deleting its row."""
    token_hash = hash_token(token)
    db.query(models.Session).filter(models.Session.token_hash == token_hash).delete()


def revoke_sessions_for_user(db: Session, user_id: int) -> None:
    """Revoke all sessions for a user."""
    db.query(models.Session).filter(models.Session.user_id == user_id).delete()


def set_session_cookie(
    response,
    *,
    token: str,
    secure: bool,
    max_age_seconds: int = SESSION_MAX_AGE_SECONDS,
    name: str = SESSION_COOKIE_NAME,
) -> None:
    """Set the opaque session cookie (HttpOnly, SameSite=Lax)."""
    response.set_cookie(
        key=name,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response, *, name: str = SESSION_COOKIE_NAME) -> None:
    response.delete_cookie(key=name, path="/", httponly=True, samesite="lax", secure=False)
