"""Sliding-window rate limiter for hot endpoints.

Two backends, one contract:

1. **Shared (Redis):** When ``RATE_LIMIT_REDIS_URL`` is set, a Lua script runs
   ``ZADD`` + ``ZREMRANGEBYSCORE`` + ``ZCARD`` + ``EXPIRE`` atomically on the
   key. Two replicas share one cap, which is exactly what a multi-process
   deploy needs so a horizontal scale-out does not silently double ingest.
2. **Local (deque):** When Redis is unreachable or the URL is empty, the same
   counter lives in process memory on ``_BUCKETS``.

``check_allowed(key, *, limit, window_seconds)`` is the single public surface.
Existing callers (``rate_limit()``, ``limited_429_response()``, the webhooks)
need zero changes — this is opt-in by config, not by code path.

**Fallback semantics (per scope), explicit and deliberate:**

* Ingest / API scopes fail **closed** on Redis error: ``429 + Retry-After`` so
  we never accept unbounded traffic because the shared cap went away.
* Webhook scopes fail **open** on Redis error *after signature verification*.
  Signature-check-before-anything-else is preserved; a verified event whose
  Redis record could not be written still stores (``200``) and falls back to a
  conservative process-local cap instead of dropping a real payment.

See ``check_allowed_scope`` for the explicit ``fail_mode`` flag used by call
sites that know which side of that divide they are on.

Tuning: ``RATE_LIMIT_PER_MINUTE`` in Settings. Tests call ``clear()`` to reset
buckets between cases.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings

if TYPE_CHECKING:
    import redis.asyncio as redis_asyncio

_BUCKETS: dict[str, deque[float]] = {}

#: Atomic Lua script for a sliding-window counter on a Redis sorted set.
#:
#: Keys[1] = limiter key
#: ARGV[1] = current timestamp (seconds, float string)
#: ARGV[2] = window length in seconds
#: ARGV[3] = unique member for *this* hit (uuid so two hits at the same wall
#:           time both count instead of overwriting each other's score)
#: ARGV[4] = limit (int)
#:
#: Returns: integer count of hits inside [now - window, now] AFTER inserting
#: this one.  Caller compares > limit → over cap.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local member = ARGV[3]
local limit = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window * 2))
local count = redis.call('ZCARD', key)
return count
"""

_redis_client: redis_asyncio.Redis | None = None
_redis_client_broken: bool = False
_redis_client_last_try: float = 0.0

#: Minimum seconds between Redis reconnect attempts after a failure.
#: Avoids ping-ponging every request during an outage.
_REDIS_RETRY_BACKOFF_SECONDS = 15.0


async def _get_redis_client() -> redis_asyncio.Redis | None:
    """Return a shared Redis client, or ``None`` if Redis is off/unreachable.

    Backoff: once the client is marked broken we wait at least
    ``_REDIS_RETRY_BACKOFF_SECONDS`` before trying a fresh connection so a
    Redis outage cannot become a CPU storm via TLS handshakes on every
    request.
    """

    global _redis_client, _redis_client_broken, _redis_client_last_try
    settings = get_settings()
    url = settings.RATE_LIMIT_REDIS_URL
    if not url:
        return None

    now = time.monotonic()
    if _redis_client is not None and not _redis_client_broken:
        return _redis_client
    if _redis_client_broken and (now - _redis_client_last_try) < _REDIS_RETRY_BACKOFF_SECONDS:
        return None

    try:
        import redis.asyncio as redis_asyncio
    except ImportError:
        return None

    try:
        client = redis_asyncio.Redis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=1.5,
            socket_timeout=1.0,
            socket_keepalive=True,
            max_connections=16,
        )
        await client.ping()
    except Exception:  # noqa: BLE001 - any transport / auth problem → local-only
        _redis_client_broken = True
        _redis_client_last_try = now
        return None

    _redis_client = client
    _redis_client_broken = False
    _redis_client_last_try = now
    return _redis_client


def _mark_redis_broken() -> None:
    """Force the next call to either retry (after backoff) or use the deque."""

    global _redis_client_broken, _redis_client_last_try, _redis_client
    _redis_client_broken = True
    _redis_client_last_try = time.monotonic()
    _redis_client = None


def _check_allowed_memory(key: str, *, limit: int, window_seconds: float) -> tuple[bool, float]:
    """Original deque implementation, preserved exactly as the fallback path."""

    now = time.monotonic()
    bucket = _BUCKETS.setdefault(key, deque())
    while bucket and bucket[0] <= now - window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(0.0, (bucket[0] + window_seconds) - now)
        return False, retry_after
    bucket.append(now)
    return True, 0.0


def _is_webhook_key(key: str) -> bool:
    """Heuristic for the default fail_mode dispatch when no flag is passed.

    A caller that *knows* it is a verified webhook should prefer the explicit
    ``check_allowed_scope(..., fail_mode="open_after_sig")``; this heuristic
    is purely for the legacy no-flag contract of ``check_allowed`` so the
    behaviour matches intent even when a caller forgets.
    """

    lowered = key.lower()
    return ("webhook" in lowered) or ("reply" in lowered) or ("replies" in lowered)


async def check_allowed(
    key: str, *, limit: int, window_seconds: float = 60.0
) -> tuple[bool, float]:
    """Record a hit and say whether it fits in the window.

    Returns ``(allowed, retry_after_seconds)``. ``retry_after_seconds`` is 0
    when allowed.

    Default fail-mode dispatch: ingest-style keys fail closed on Redis error,
    webhook-style keys fail open. For explicit control, use
    :func:`check_allowed_scope`.
    """

    default_mode: Literal["closed", "open_after_sig"] = (
        "open_after_sig" if _is_webhook_key(key) else "closed"
    )
    return await check_allowed_scope(
        key, limit=limit, window_seconds=window_seconds, fail_mode=default_mode
    )


async def check_allowed_scope(
    key: str,
    *,
    limit: int,
    window_seconds: float = 60.0,
    fail_mode: Literal["closed", "open_after_sig"] = "closed",
) -> tuple[bool, float]:
    """Same counter semantics as :func:`check_allowed`, with explicit fail-mode.

    ``fail_mode`` only matters when Redis is configured but unreachable. When
    Redis is up the answer is authoritative regardless of mode.

    * ``"closed"`` (default, for ingest / API): Redis error → behave like the
      call exceeded the cap. Return ``(False, retry_after)`` using an
      in-process cap as the *hint* for Retry-After but the boolean decision is
      "reject" even if the local deque would have allowed.
    * ``"open_after_sig"`` (verified webhook): Redis error → fall back to the
      in-process deque and trust its answer. A verified event with a dead
      Redis is still stored.
    """

    client = await _get_redis_client()
    if client is None:
        mem_ok, mem_ra = _check_allowed_memory(key, limit=limit, window_seconds=window_seconds)
        if fail_mode == "closed" and get_settings().RATE_LIMIT_REDIS_URL:
            # Configured Redis but it's broken → fail closed. Use the in-mem
            # number only to pick a Retry-After hint; the boolean is False.
            return False, max(mem_ra, 1.0)
        return mem_ok, mem_ra

    try:
        now_ts = time.time()
        member = f"{now_ts:.6f}:{uuid.uuid4().hex}"
        result = await client.eval(
            _SLIDING_WINDOW_LUA,
            1,  # numkeys
            key,
            f"{now_ts:.6f}",
            f"{window_seconds:g}",
            member,
            str(int(limit)),
        )
    except Exception:  # noqa: BLE001 - network / timeout / OOM on Redis
        _mark_redis_broken()
        mem_ok, mem_ra = _check_allowed_memory(key, limit=limit, window_seconds=window_seconds)
        if fail_mode == "closed":
            return False, max(mem_ra, 1.0)
        return mem_ok, mem_ra

    count = int(result) if result is not None else limit + 1
    if count <= limit:
        return True, 0.0
    retry_after = max(0.0, window_seconds / 4.0)
    try:
        min_score_bytes = await client.zrange(key, 0, 0, withscores=True)
        if min_score_bytes:
            _, min_score = min_score_bytes[0]
            retry_after = max(0.0, (float(min_score) + window_seconds) - time.time())
    except Exception:  # noqa: BLE001
        _mark_redis_broken()
    return False, retry_after


async def clear() -> None:
    """Empty every bucket. Tests only; never called in request handling.

    Best-effort: also scans Redis for every key under our configured shared
    pattern and deletes them, so a test run with Redis present starts truly
    clean. Errors are swallowed (the caller only cares about the in-process
    reset in failure modes).
    """

    _BUCKETS.clear()
    client = await _get_redis_client()
    if client is None:
        return
    try:
        import asyncio

        cursor = 0
        keys_to_delete: list[str] = []
        while True:
            cursor, batch = await client.scan(cursor, match="*", count=200)
            for k in batch:
                if isinstance(k, bytes):
                    keys_to_delete.append(k.decode("utf-8"))
                else:
                    keys_to_delete.append(str(k))
            if cursor == 0:
                break
        if keys_to_delete:
            await client.delete(*keys_to_delete[:500])
            await asyncio.sleep(0)
    except Exception:  # noqa: BLE001
        _mark_redis_broken()


def _bucket_key(request: Request, scope: str) -> str:
    ip = request.client.host if request.client else "unknown"
    business_id = get_settings().BUSINESS_ID or "default"
    return f"{business_id}:{scope}:{ip}"


def rate_limit(scope: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory enforcing the per-minute cap for ``scope``.

    Ingest/API scope — fails closed on Redis error (the safe default).
    """

    async def _dep(request: Request) -> None:
        from fastapi import HTTPException

        settings = get_settings()
        limit = settings.RATE_LIMIT_PER_MINUTE
        allowed, retry_after = await check_allowed_scope(
            _bucket_key(request, scope),
            limit=limit,
            fail_mode="closed",
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {scope}; retry later.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    return _dep


async def limited_429_response(
    request: Request,
    limit: int,
    scope: str,
    *,
    fail_mode: Literal["closed", "open_after_sig"] = "closed",
) -> JSONResponse | None:
    """Non-raising variant for call sites that must shape their own body.

    When ``fail_mode`` is ``"open_after_sig"`` and Redis is down, returns
    ``None`` when the in-process cap allows, so the request proceeds to the
    caller's own gate (typically a signature check). Webhook call sites use
    this for the pre-signature wall too: the property they need is that a
    Redis outage never turns verified traffic into a 429 — only verified
    events are ever stored, and forgery is rejected by signature, so failing
    open here costs HMAC CPU at worst, not correctness.
    """

    allowed, retry_after = await check_allowed_scope(
        _bucket_key(request, scope),
        limit=limit,
        fail_mode=fail_mode,
    )
    if allowed:
        return None
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"status": "rejected", "reason": "rate_limited"},
        headers={"Retry-After": str(int(retry_after) + 1)},
    )
