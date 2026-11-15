"""Application configuration using pydantic-settings.

Production validates required secrets and fails clearly without printing secrets.
"""

from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Runtime
    ENV: str = "development"
    PORT: int = 8000

    # Database
    DATABASE_URL: str
    TEST_DATABASE_URL: str

    # Public URLs
    PUBLIC_APP_URL: str = "http://localhost:5173"
    PUBLIC_API_URL: str = "http://localhost:5173/api"
    # Allowed frontend origins (comma-separated) for CORS and trusted-origin checks.
    # Includes localhost for local development plus production/preview Vercel domains.
    ALLOWED_ORIGINS: str = "http://localhost:5173"

    @property
    def allowed_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    # Session cookie
    SESSION_COOKIE_NAME: str = "ecommerce_session"
    SESSION_MAX_AGE_SECONDS: int = 1209600  # 14 days
    SESSION_COOKIE_SECURE: bool = False

    # Rate limiting
    RATE_LIMIT_HMAC_KEY: str
    TRUSTED_PROXY_CIDRS: str = ""

    # Razorpay
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    RAZORPAY_WEBHOOK_PUBLIC_URL: str = ""
    # When true, Razorpay calls target the TEST environment. With real
    # ``rzp_test_...`` credentials this creates genuine provider test orders via
    # the SDK. Without a real-looking key it falls back to mocked ids, which are
    # prohibited in production (see validator).
    RAZORPAY_TEST_MODE: bool = True

    # Store / money
    STORE_CURRENCY: str = "USD"
    SHIPPING_FLAT_RATE_MINOR: int = 0
    DOWNLOAD_LINK_TTL_SECONDS: int = 900

    # Storage
    UPLOAD_MAX_IMAGE_BYTES: int = 5242880
    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_DIRECTORY: str = "./storage"
    STORAGE_BUCKET: str = ""
    STORAGE_ENDPOINT_URL: str = ""
    STORAGE_ACCESS_KEY_ID: str = ""
    STORAGE_SECRET_ACCESS_KEY: str = ""

    # OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_CALLBACK_URL: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_CALLBACK_URL: str = ""
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_CALLBACK_URL: str = ""

    # Email
    EMAIL_MODE: str = "console"

    # Seed admin (development only)
    SEED_ADMIN_EMAIL: str = "admin@example.com"
    SEED_ADMIN_PASSWORD: str = ""

    @field_validator("DATABASE_URL", "TEST_DATABASE_URL")
    @classmethod
    def _validate_postgres_url(cls, v: str) -> str:
        if not v.startswith("postgresql+psycopg://"):
            if v.startswith("postgresql://"):
                v = v.replace("postgresql://", "postgresql+psycopg://", 1)
            else:
                raise ValueError("DATABASE_URL must use postgresql+psycopg:// (PostgreSQL only)")
        return v

    @field_validator("TRUSTED_PROXY_CIDRS")
    @classmethod
    def _split_cidrs(cls, v: str) -> str:
        # Stored as a comma-separated string; parsed in rate_limits/main.
        return v

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        if self.ENV == "production":
            required = [
                "DATABASE_URL",
                "RATE_LIMIT_HMAC_KEY",
                "RAZORPAY_KEY_SECRET",
                "RAZORPAY_WEBHOOK_SECRET",
                "STORAGE_SECRET_ACCESS_KEY",
                "SEED_ADMIN_PASSWORD",
            ]
            missing = [name for name in required if not getattr(self, name)]
            if missing:
                raise ValueError(
                    "Production requires settings: " + ", ".join(missing)
                )
            if self.SESSION_COOKIE_SECURE is not True:
                raise ValueError("SESSION_COOKIE_SECURE must be true in production")
            if not (self.PUBLIC_APP_URL.startswith("https://") and self.PUBLIC_API_URL.startswith("https://")):
                raise ValueError("PUBLIC_APP_URL and PUBLIC_API_URL must use HTTPS in production")
            if not self.TRUSTED_PROXY_CIDRS:
                raise ValueError("TRUSTED_PROXY_CIDRS must be configured in production")

            # Razorpay credentials must be real in production. Mock/fabricated
            # provider ids are prohibited in production.
            if not self.RAZORPAY_KEY_ID:
                raise ValueError("RAZORPAY_KEY_ID is required in production")
            if not self.RAZORPAY_KEY_SECRET:
                raise ValueError("RAZORPAY_KEY_SECRET is required in production")
            if self.RAZORPAY_TEST_MODE:
                if not self.RAZORPAY_KEY_ID.startswith("rzp_test_"):
                    raise ValueError(
                        "In production with RAZORPAY_TEST_MODE=true, RAZORPAY_KEY_ID "
                        "must be a real Razorpay test key (rzp_test_...). Mock keys are prohibited."
                    )
            else:
                if not self.RAZORPAY_KEY_ID.startswith("rzp_live_"):
                    raise ValueError(
                        "In production live mode, RAZORPAY_KEY_ID must be a real "
                        "Razorpay live key (rzp_live_...)."
                    )
        return self

    @property
    def trusted_proxy_cidrs(self) -> List[str]:
        if not self.TRUSTED_PROXY_CIDRS:
            return []
        return [c.strip() for c in self.TRUSTED_PROXY_CIDRS.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_settings_uncached() -> Settings:
    return Settings()
