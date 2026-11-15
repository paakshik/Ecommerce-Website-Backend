"""FastAPI application: middleware, JSON errors, router mounting."""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import rate_limits
from app.config import get_settings
from app.routers import admin, auth, cart, downloads, oauth, orders, products, reviews, uploads, webhooks, wishlists

settings = get_settings()

app = FastAPI(title="Ecommerce API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_response(status_code: int, code: str, message: str, headers=None):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


@app.exception_handler(rate_limits.RateLimitStoreFailure)
async def rate_limit_store_failure_handler(request: Request, exc: rate_limits.RateLimitStoreFailure):
    return _error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "rate_limit_store_unavailable",
        "Rate limit store unavailable. Try again later.",
        headers={"Cache-Control": "no-store"},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        content = detail
    elif isinstance(detail, dict):
        content = {"error": {"code": "error", "message": str(detail)}}
    else:
        content = {"error": {"code": "error", "message": str(detail)}}
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internals; log in real deployments.
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "An unexpected error occurred.",
    )


@app.on_event("startup")
def _verify_razorpay_on_startup():
    """Fail fast at startup/redeploy if Razorpay credentials are invalid.

    Only runs when a real provider (live/test) is configured; mock/dev mode is
    skipped so local development without credentials keeps working. The check is
    also skipped under pytest (ENV=testing) because tests control provider
    behavior via monkeypatch. Changing credentials requires a backend restart/
    redeployment.
    """
    from app.services import razorpay

    if razorpay.settings.ENV == "testing":
        return
    if razorpay.provider_mode() != "mock":
        razorpay.verify_razorpay_credentials()


@app.get("/api/health")
async def health():
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "db": "postgresql"}
    except Exception:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"ok": False, "db": "postgresql"})
    finally:
        db.close()


# Mount routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(oauth.router, prefix="/api/auth/oauth", tags=["oauth"])
app.include_router(products.router, prefix="/api", tags=["products"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(cart.router, prefix="/api", tags=["cart"])
app.include_router(orders.router, prefix="/api", tags=["orders"])
app.include_router(webhooks.router, prefix="/api", tags=["webhooks"])
app.include_router(uploads.router, prefix="/api", tags=["uploads"])
app.include_router(downloads.router, prefix="/api", tags=["downloads"])
app.include_router(wishlists.router, prefix="/api", tags=["wishlist"])
app.include_router(reviews.router, prefix="/api", tags=["reviews"])
