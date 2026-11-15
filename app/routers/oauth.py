"""OAuth router: Google, GitHub, Microsoft (state + PKCE).

No silent email linking: an existing account is linked only via the
authenticated account-linking flow; otherwise a new account is created.
"""

import base64
import hashlib
import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import auth as auth_mod, models, oauth as oauth_mod
from app.database import get_db
from app.dependencies import current_user, csrf_valid
from app.schemas import UserOut

router = APIRouter()

APP_SUCCESS_URL = __import__("app.config", fromlist=["get_settings"]).get_settings().PUBLIC_APP_URL


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _provider_email(provider: str, userinfo: dict) -> Optional[str]:
    if provider == "google":
        return userinfo.get("email")
    if provider == "github":
        return userinfo.get("email")
    if provider == "microsoft":
        return userinfo.get("mail") or userinfo.get("userPrincipalName")
    return None


@router.get("/{provider}/redirect")
def oauth_redirect(
    provider: str,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
):
    """Start OAuth: store state + PKCE verifier in session, redirect to provider."""
    try:
        oauth_mod.get_provider(provider)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "unknown_provider", "message": "Unknown OAuth provider"}})
    provider_cfg = oauth_mod.OAUTH_PROVIDERS[provider]
    if not provider_cfg["client_id"] or not provider_cfg["client_secret"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": {"code": "oauth_not_configured", "message": "OAuth provider not configured"}})

    # Ensure a session exists to bind the state to.
    session = None
    if session_cookie:
        session = auth_mod.get_session(db, session_cookie)
    if session is None:
        raw_token = auth_mod.create_session(db, user_id=None)
        db.commit()
        auth_mod.set_session_cookie(response, token=raw_token, secure=__import__("app.config", fromlist=["get_settings"]).get_settings().SESSION_COOKIE_SECURE)
        session = auth_mod.get_session(db, raw_token)

    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(32) if provider_cfg["pkce"] else None
    code_challenge = _code_challenge(code_verifier) if code_verifier else None

    session.oauth_state = state
    session.oauth_code_verifier = code_verifier
    db.add(session)
    db.commit()

    client = oauth_mod.make_client(provider)
    auth_url, _ = client.create_authorization_url(
        provider_cfg["authorize_endpoint"],
        state=state,
        code_challenge=code_challenge,
        code_challenge_method="S256" if code_verifier else None,
        redirect_uri=provider_cfg["redirect_uri"],
    )
    return RedirectResponse(auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: Optional[str] = Cookie(default=None, alias=auth_mod.SESSION_COOKIE_NAME),
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
):
    """OAuth callback: validate state, exchange code, create/link account, set session."""
    try:
        oauth_mod.get_provider(provider)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "unknown_provider", "message": "Unknown OAuth provider"}})
    provider_cfg = oauth_mod.OAUTH_PROVIDERS[provider]

    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "missing_session", "message": "Session required for OAuth callback"}})
    session = auth_mod.get_session(db, session_cookie)
    if session is None or session.oauth_state != state or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "invalid_oauth_state", "message": "Invalid or missing OAuth state"}})

    code_verifier = session.oauth_code_verifier
    session.oauth_state = None
    session.oauth_code_verifier = None
    db.add(session)
    db.commit()

    # Exchange code for token.
    import httpx
    client = oauth_mod.make_client(provider)
    try:
        token = client.fetch_token(
            provider_cfg["token_endpoint"],
            code=code,
            code_verifier=code_verifier,
            redirect_uri=provider_cfg["redirect_uri"],
        )
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "oauth_token_failed", "message": "OAuth token exchange failed"}})

    # Fetch user profile.
    access_token = token.get("access_token")
    try:
        resp = httpx.get(provider_cfg["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
        resp.raise_for_status()
        userinfo = resp.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "oauth_profile_failed", "message": "Failed to fetch OAuth profile"}})

    email = _provider_email(provider, userinfo)
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": {"code": "oauth_no_email", "message": "OAuth provider did not return an email"}})
    email = email.strip().lower()

    # Link by provider+provider_user_id only.
    provider_user_id = str(userinfo.get("id") or userinfo.get("sub") or "")
    oauth_account = (
        db.query(models.OAuthAccount)
        .filter(models.OAuthAccount.provider == provider)
        .filter(models.OAuthAccount.provider_user_id == provider_user_id)
        .first()
    )
    if oauth_account:
        user = oauth_account.user
    else:
        # Do not silently link by email match. If email exists, require linking flow.
        existing_user = db.query(models.User).filter(models.User.email == email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "account_link_required", "message": f"An account with this email exists. Sign in and link your {provider} account."}},
            )
        user = models.User(email=email, password_hash=None, role="customer")
        db.add(user)
        db.flush()
        db.add(
            models.OAuthAccount(
                user_id=user.id,
                provider=provider,
                provider_user_id=provider_user_id,
                provider_email=email,
            )
        )

    # Rotate/create session for the user.
    existing_session = auth_mod.get_session(db, session_cookie)
    if existing_session and existing_session.user_id == user.id:
        raw_token = auth_mod.rotate_session(db, existing_session, user_id=user.id)
    else:
        raw_token = auth_mod.create_session(db, user_id=user.id)
    db.commit()
    auth_mod.set_session_cookie(response, token=raw_token, secure=__import__("app.config", fromlist=["get_settings"]).get_settings().SESSION_COOKIE_SECURE)

    # Redirect to app (the callback page completes auth on the client).
    return RedirectResponse(f"{APP_SUCCESS_URL.rstrip('/')}/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post("/link/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def oauth_link(
    provider: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(current_user),
):
    """Placeholder: authenticated account links a provider (requires CSRF + session)."""
    # Full linking flow: verify OAuth state, bind oauth_accounts to current user.
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail={"error": {"code": "not_implemented", "message": "Account linking not yet implemented"}})
