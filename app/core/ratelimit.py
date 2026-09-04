"""Process-local sliding-window rate limiter for hot endpoints.

Why this exists: ``POST /invoices/batch`` (ingest) and both webhooks are
reachable by non-operators -- webhooks must be, they are called by Razorpay
and Resend, not by us. Without a cap, a misbehaving sender or a replay loop
can fill the database or burn LLM budget. This is the cheap, dependency-free
half: per-IP sliding windows held in memory.

Limits of this approach, stated plainly: state is per-process, so two
replicas allow twice the rate. A deploy that needs an exact global cap
should enforce it one layer up (reverse proxy / Redis). What this module
guarantees even then is that a single process degrades with 429 + Retry-After
instead of falling over.

Tuning: ``RATE_LIMIT_PER_MINUTE`` in Settings. Tests call ``clear()`` to
reset buckets between cases.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings

_BUCKETS: dict[str, deque[float]] = {}


def _bucket_key(request: Request, scope: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{scope}:{ip}"


def check_allowed(key: str, *, limit: int, window_seconds: float = 60.0) -> tuple[bool, float]:
    """Record a hit and say whether it fits in the window.

    Returns ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is 0
    when allowed.
    """

    now = time.monotonic()
    bucket = _BUCKETS.setdefault(key, deque())
    while bucket and bucket[0] <= now - window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(0.0, (bucket[0] + window_seconds) - now)
        return False, retry_after
    bucket.append(now)
    return True, 0.0


def clear() -> None:
    """Empty every bucket. Tests only; never called in request handling."""
    _BUCKETS.clear()


def rate_limit(scope: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory enforcing the per-minute cap for ``scope``."""

    async def _dep(request: Request) -> None:
        from fastapi import HTTPException

        limit = get_settings().RATE_LIMIT_PER_MINUTE
        allowed, retry_after = check_allowed(_bucket_key(request, scope), limit=limit)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {scope}; retry later.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return _dep


async def limited_429_response(request: Request, limit: int, scope: str) -> JSONResponse | None:
    """Non-raising variant for webhook handlers that must shape their own body."""
    allowed, retry_after = check_allowed(_bucket_key(request, scope), limit=limit)
    if allowed:
        return None
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"status": "rejected", "reason": "rate_limited"},
        headers={"Retry-After": str(int(retry_after) + 1)},
    )
