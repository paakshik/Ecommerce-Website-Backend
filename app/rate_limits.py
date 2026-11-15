"""Shared PostgreSQL sliding-window rate limiter (no process-local state).

Exact quotas (Section 10 of the plan):
  Login:    5/email/15min + 20/IP/15min
  Register: 3/email/hr    + 5/IP/hr
  Checkout: 5/user/min    + 20/IP/min
  Upload:   10/admin/min  + 30/IP/min

- HMAC-digested subjects (raw emails/IPs never stored).
- Deterministic bucket locking order to avoid deadlocks.
- DB-time pruning of timestamps outside (now - window, now].
- Check count, append if permitted, commit before the protected op.
- Reject exhausted buckets without appending.
- Retry-After = time until oldest active admission expires (ceil, positive int).
- 503 on limiter store failure, no side effects.
- Trusted proxy only; no arbitrary X-Forwarded-For; IPv4/IPv6 normalized.
"""

import hashlib
import hmac
import math
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import psycopg
from fastapi import HTTPException, Request, status
from sqlalchemy import text

from app.config import get_settings

settings = get_settings()

# Scope -> (limit, window_seconds)
QUOTAS = {
    "login_email": (5, 15 * 60),
    "login_ip": (20, 15 * 60),
    "register_email": (3, 60 * 60),
    "register_ip": (5, 60 * 60),
    "checkout_user": (5, 60),
    "checkout_ip": (20, 60),
    "upload_admin": (10, 60),
    "upload_ip": (30, 60),
}


class RateLimitStoreFailure(Exception):
    """Raised when the rate-limit store (PostgreSQL) is unavailable."""


def _digest(subject: str) -> str:
    """HMAC-SHA256 digest of a subject string (hex)."""
    key = settings.RATE_LIMIT_HMAC_KEY.encode("utf-8")
    return hmac.new(key, subject.encode("utf-8"), hashlib.sha256).hexdigest()


def _parse_client_ip(request: Request) -> Optional[str]:
    """Resolve the direct peer IP unless a trusted proxy is configured.

    Never trusts arbitrary client-supplied X-Forwarded-For. Only honors the
    forwarding header when the direct peer is a configured trusted proxy.
    """
    client = request.client
    peer_ip = client.host if client else None
    if not peer_ip:
        return None

    try:
        peer_addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        peer_addr = None

    trusted = settings.trusted_proxy_cidrs
    forwarded = request.headers.get("x-forwarded-for")
    if trusted and forwarded:
        networks = [ipaddress.ip_network(cidr.strip(), strict=False) for cidr in trusted if cidr.strip()]
        if peer_addr is not None and any(peer_addr in net for net in networks):
            first = forwarded.split(",")[0].strip()
            try:
                return str(ipaddress.ip_address(first))
            except ValueError:
                pass

    return _normalize_ip(peer_ip)


def _normalize_ip(ip: str) -> str:
    """Normalize IPv4/IPv6 consistently (IPv4-mapped IPv6 -> IPv4)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return str(addr)


def _connect():
    """Open a raw psycopg connection to the rate-limit database."""
    url = settings.DATABASE_URL
    if url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url, autocommit=False)


def _db_now(conn) -> datetime:
    """Return the database server's current time (timezone-aware).

    Accepts either a raw psycopg connection or a SQLAlchemy Session.
    """
    if hasattr(conn, "execute") and not hasattr(conn, "cursor"):
        # SQLAlchemy Session (has execute but not cursor)
        row = conn.execute(text("SELECT clock_timestamp() AT TIME ZONE 'UTC'")).first()
    else:
        # Raw psycopg connection (has cursor)
        with conn.cursor() as cur:
            cur.execute("SELECT clock_timestamp() AT TIME ZONE 'UTC'")
            row = cur.fetchone()
    now = row[0]
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now


def _ensure_bucket(conn, scope: str, digest: str) -> None:
    """Create missing bucket row (conflict-safe)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rate_limit_buckets (scope, subject_digest, admitted, expires_at)
            VALUES (%s, %s, '{}'::timestamptz[], now() + make_interval(secs => 86400))
            ON CONFLICT (scope, subject_digest) DO NOTHING
            """,
            (scope, digest),
        )


def _lock_bucket(conn, scope: str, digest: str) -> None:
    """Acquire FOR UPDATE row lock on the bucket."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM rate_limit_buckets
            WHERE scope = %s AND subject_digest = %s
            FOR UPDATE
            """,
            (scope, digest),
        )


def _read_admitted(conn, scope: str, digest: str) -> list:
    """Read the admitted timestamp array for a bucket."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT admitted FROM rate_limit_buckets
            WHERE scope = %s AND subject_digest = %s
            """,
            (scope, digest),
        )
        row = cur.fetchone()
    return row[0] if row else []


def _write_admitted(conn, scope: str, digest: str, admitted: list) -> None:
    """Write the admitted timestamp array and refresh expiry."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE rate_limit_buckets
            SET admitted = %s::timestamptz[],
                expires_at = now() + make_interval(secs => 86400)
            WHERE scope = %s AND subject_digest = %s
            """,
            (admitted, scope, digest),
        )


def _compute_retry_after(admitted: list, window_seconds: int, now: datetime) -> int:
    """Retry-After seconds from oldest active admission, ceil to positive int."""
    window_start = now - timedelta(seconds=window_seconds)
    active = [t for t in admitted if t is not None and t > window_start and t <= now]
    if active:
        oldest = min(active)
        delay = (oldest + timedelta(seconds=window_seconds)) - now
    else:
        delay = timedelta(seconds=1)
    return max(1, math.ceil(delay.total_seconds()))


def _check_one(conn, scope: str, digest: str, limit: int, window_seconds: int) -> Tuple[bool, Optional[int]]:
    """Check one bucket under its FOR UPDATE lock.

    Reads fresh DB state under the lock, appends if under the limit, commits.
    Returns (allowed, retry_after_seconds).
    """
    now = _db_now(conn)
    window_start = now - timedelta(seconds=window_seconds)

    admitted = _read_admitted(conn, scope, digest)
    active = [t for t in admitted if t is not None and t > window_start and t <= now]

    if len(active) >= limit:
        return False, _compute_retry_after(admitted, window_seconds, now)

    active.append(now)
    _write_admitted(conn, scope, digest, active)
    conn.commit()
    return True, None


def check_bucket(
    db: Session,
    scope: str,
    subject_digest: str,
    limit: int,
    window_seconds: int,
) -> Tuple[bool, Optional[int]]:
    """Check one bucket atomically under FOR UPDATE row lock.

    Uses an independent raw psycopg connection so the FOR UPDATE lock is held
    for the entire read-check-append-commit cycle, eliminating lost updates
    under concurrent sessions. The `db` parameter is retained for API
    compatibility with FastAPI dependency injection.

    Returns (allowed, retry_after_seconds). When not allowed, retry_after is a
    positive int and nothing is appended.
    """
    conn = None
    try:
        conn = _connect()
        _ensure_bucket(conn, scope, subject_digest)
        _lock_bucket(conn, scope, subject_digest)
        allowed, retry = _check_one(conn, scope, subject_digest, limit, window_seconds)
        if not allowed:
            conn.rollback()
        return allowed, retry
    except RateLimitStoreFailure:
        raise
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise RateLimitStoreFailure(str(exc)) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def check_multi(
    db: Session,
    checks: List[Tuple[str, str, int, int]],
) -> Tuple[bool, Optional[int]]:
    """Check multiple buckets atomically under FOR UPDATE row locks.

    Locks all buckets in deterministic order, then evaluates each: read fresh
    DB state under the lock, append if under the limit. Rejects if any bucket
    is exhausted (max retry_after across exhausted buckets). Nothing is
    appended for any bucket when rejected.

    Returns (allowed, retry_after_seconds).
    """
    conn = None
    try:
        conn = _connect()
        keys = [(scope, digest) for scope, digest, _, _ in checks]
        for scope, digest in sorted(set(keys)):
            _ensure_bucket(conn, scope, digest)
            _lock_bucket(conn, scope, digest)
        now = _db_now(conn)
        worst_retry: Optional[int] = None
        to_append: List[Tuple[str, str, list]] = []

        for scope, digest, limit, window_seconds in checks:
            admitted = _read_admitted(conn, scope, digest)
            window_start = now - timedelta(seconds=window_seconds)
            active = [t for t in admitted if t is not None and t > window_start and t <= now]

            if len(active) >= limit:
                seconds = _compute_retry_after(admitted, window_seconds, now)
                if worst_retry is None or seconds > worst_retry:
                    worst_retry = seconds
            else:
                active.append(now)
                to_append.append((scope, digest, active))

        if worst_retry is not None:
            conn.rollback()
            return False, worst_retry

        for scope, digest, active_list in to_append:
            _write_admitted(conn, scope, digest, active_list)
        conn.commit()
        return True, None
    except RateLimitStoreFailure:
        raise
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise RateLimitStoreFailure(str(exc)) from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def rate_limit_response(retry_after: int) -> HTTPException:
    """Build the standard 429 response per the error contract."""
    headers = {
        "Retry-After": str(retry_after),
        "Cache-Control": "no-store",
    }
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "rate_limit_exceeded",
                "message": "Too many requests. Try again later.",
                "retry_after_seconds": retry_after,
            }
        },
        headers=headers,
    )


def limiter_store_failure_response() -> HTTPException:
    """503 response when the limiter store fails (no side effects)."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": {"code": "rate_limit_store_unavailable", "message": "Rate limit store unavailable. Try again later."}},
        headers={"Cache-Control": "no-store"},
    )
