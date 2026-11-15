"""OAuth client configuration (Authlib) for Google, GitHub and Microsoft.

Uses state + PKCE where supported. Does not silently link providers by email;
linking requires an authenticated account's explicit approval (handled in the router).
"""

from authlib.integrations.httpx_client import AsyncOAuth2Client
from app.config import get_settings

settings = get_settings()

OAUTH_PROVIDERS = {
    "google": {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_CALLBACK_URL,
        "authorize_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": ["openid", "email", "profile"],
        "pkce": True,
    },
    "github": {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "redirect_uri": settings.GITHUB_CALLBACK_URL,
        "authorize_endpoint": "https://github.com/login/oauth/authorize",
        "token_endpoint": "https://github.com/login/oauth/access_token",
        "userinfo_endpoint": "https://api.github.com/user",
        "scopes": ["read:user", "user:email"],
        "pkce": False,
    },
    "microsoft": {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,
        "redirect_uri": settings.MICROSOFT_CALLBACK_URL,
        "authorize_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_endpoint": "https://graph.microsoft.com/v1.0/me",
        "scopes": ["openid", "email", "profile"],
        "pkce": True,
    },
}


def get_provider(name: str) -> dict:
    if name not in OAUTH_PROVIDERS:
        raise KeyError(f"Unknown OAuth provider: {name}")
    return OAUTH_PROVIDERS[name]


def make_client(name: str) -> AsyncOAuth2Client:
    provider = get_provider(name)
    return AsyncOAuth2Client(
        client_id=provider["client_id"],
        client_secret=provider["client_secret"],
        redirect_uri=provider["redirect_uri"],
        scope=" ".join(provider["scopes"]),
    )
