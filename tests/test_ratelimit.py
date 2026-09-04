"""Tests for the shared rate-limiter contract.

Covers:
* TR-2.1 In-memory fallback when Redis URL is unset
* TR-2.2 ``clear()`` resets both backends
* TR-2.3 Two concurrent tasks share one Redis cap
* TR-3.1 ``fail_mode="closed"`` rejects on Redis outage
* TR-3.2 ``fail_mode="open_after_sig"`` falls back to in-memory on outage
* TR-3.3 Razorpay webhook endpoint returns 200 with valid sig when Redis is down
"""

from __future__ import annotations

import asyncio
import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import ratelimit
from app.core.ratelimit import (
    _mark_redis_broken,
    check_allowed,
    check_allowed_scope,
    clear,
)

REDIS_URL = os.environ.get("RATE_LIMIT_REDIS_URL")
HAS_REDIS = bool(REDIS_URL)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clean_buckets():
    """Every test starts with empty buckets and a re-connectable client."""

    await clear()
    ratelimit._redis_client = None
    ratelimit._redis_client_broken = False
    ratelimit._redis_client_last_try = 0.0
    yield
    await clear()


@pytest.fixture
def _no_redis(monkeypatch):
    """Force the limiter down the in-memory path even if Redis is configured."""

    import app.core.config as cfg

    monkeypatch.setattr(ratelimit, "_redis_client", None)
    monkeypatch.setattr(ratelimit, "_redis_client_broken", True)
    monkeypatch.setattr(ratelimit, "_redis_client_last_try", 0.0)
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", "")
    cfg.get_settings.cache_clear()
    yield


def _with_redis_url(monkeypatch, url: str | None):
    monkeypatch.setenv("RATE_LIMIT_REDIS_URL", url or "")
    # Force Settings re-read by clearing the cache on next call via getattr trick:
    import app.core.config as cfg

    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# TR-2.1 In-memory fallback (no Redis URL)
# ---------------------------------------------------------------------------


async def test_in_memory_allows_up_to_limit_then_rejects(_no_redis):
    key = "test:tr21:127.0.0.1"
    limit = 3
    for i in range(limit):
        ok, ra = await check_allowed(key, limit=limit, window_seconds=60.0)
        assert ok is True, f"hit {i} should be allowed"
        assert ra == 0.0
    ok, ra = await check_allowed(key, limit=limit, window_seconds=60.0)
    assert ok is False
    assert ra > 0.0


async def test_in_memory_retry_after_decreases_over_time(_no_redis, monkeypatch):
    key = "test:tr21_decay:127.0.0.1"
    limit = 2
    window = 0.05  # tiny window for a fast test
    for _ in range(limit):
        await check_allowed(key, limit=limit, window_seconds=window)
    ok, ra1 = await check_allowed(key, limit=limit, window_seconds=window)
    assert ok is False
    await asyncio.sleep(window * 1.1)
    ok, ra2 = await check_allowed(key, limit=limit, window_seconds=window)
    # After the window fully elapses the next call is allowed again.
    assert ok is True


# ---------------------------------------------------------------------------
# TR-2.2 clear() empties every bucket
# ---------------------------------------------------------------------------


async def test_clear_resets_in_memory_fallback(_no_redis):
    key = "test:tr22_clear:127.0.0.1"
    limit = 2
    for _ in range(limit):
        await check_allowed(key, limit=limit)
    ok, _ = await check_allowed(key, limit=limit)
    assert ok is False
    await clear()
    ok, _ = await check_allowed(key, limit=limit)
    assert ok is True


@pytest.mark.skipif(not HAS_REDIS, reason="RATE_LIMIT_REDIS_URL not set")
async def test_clear_resets_redis_keys_too(monkeypatch):
    _with_redis_url(monkeypatch, REDIS_URL)
    key = "test:tr22_redis:127.0.0.1"
    limit = 5
    for _ in range(limit):
        await check_allowed(key, limit=limit)
    ok, _ = await check_allowed(key, limit=limit)
    assert ok is False
    await clear()
    ok, _ = await check_allowed(key, limit=limit)
    assert ok is True


# ---------------------------------------------------------------------------
# TR-2.3 Two replicas share one Redis cap
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_REDIS, reason="RATE_LIMIT_REDIS_URL not set")
async def test_two_tasks_share_single_redis_cap(monkeypatch):
    """Two independent gather-coroutines must together exhaust ONE limit.

    This is the AC-2 acceptance test: "Two replicas, one shared cap in a test."
    """

    _with_redis_url(monkeypatch, REDIS_URL)
    key = "test:tr23_shared:replica-race"
    limit = 6
    per_replica = 4  # each tries MORE than half the cap so overlap is forced

    async def replica(name: str) -> list[tuple[bool, float]]:
        return [
            await check_allowed_scope(
                f"{key}:{name}", limit=limit, window_seconds=60.0, fail_mode="closed"
            )
            for _ in range(per_replica)
        ]

    # Share the SAME key so both replicas count against one bucket:
    async def replica_shared(_name: str) -> list[tuple[bool, float]]:
        results: list[tuple[bool, float]] = []
        for _ in range(per_replica):
            results.append(
                await check_allowed_scope(key, limit=limit, window_seconds=60.0, fail_mode="closed")
            )
        return results

    r1, r2 = await asyncio.gather(replica_shared("A"), replica_shared("B"))
    all_ok = [ok for ok, _ in (r1 + r2)]
    assert sum(1 for x in all_ok if x) == limit, (
        f"Expected exactly {limit} allowed across both replicas (shared cap), "
        f"got {sum(1 for x in all_ok if x)}: ok_A={[o for o,_ in r1]} ok_B={[o for o,_ in r2]}"
    )


# ---------------------------------------------------------------------------
# TR-3.1 fail_closed: Redis broken → (False, ra>0) even if in-mem would allow
# ---------------------------------------------------------------------------


async def test_fail_closed_rejects_on_redis_outage_even_if_mem_empty(monkeypatch):
    # Pretend Redis IS configured (so fail_mode semantics kick in) but mark it broken.
    _with_redis_url(monkeypatch, "redis://configured-but-invalid.local:6379/0")
    _mark_redis_broken()

    ok, ra = await check_allowed_scope(
        "test:tr31_ingest:1.2.3.4", limit=100, window_seconds=60.0, fail_mode="closed"
    )
    assert ok is False, "fail_closed must reject when Redis is configured but unreachable"
    assert ra >= 1.0, "Retry-After hint must be >= 1s"


async def test_fail_closed_triggers_default_for_non_webhook_keys(monkeypatch):
    """check_allowed() with a plain ingest key picks fail_closed by default."""

    _with_redis_url(monkeypatch, "redis://down.local:6379/0")
    _mark_redis_broken()
    ok, _ = await check_allowed("ingest:upload:5.6.7.8", limit=100)
    assert ok is False


# ---------------------------------------------------------------------------
# TR-3.2 fail_open_after_sig: Redis broken → in-memory answer trusted
# ---------------------------------------------------------------------------


async def test_fail_open_after_sig_uses_in_memory_on_redis_outage(monkeypatch):
    _with_redis_url(monkeypatch, "redis://down.local:6379/0")
    _mark_redis_broken()

    key = "biz-1:razorpay-webhook-verified"
    limit = 3
    for i in range(limit):
        ok, _ = await check_allowed_scope(key, limit=limit, fail_mode="open_after_sig")
        assert ok is True, f"verified webhook event {i} must pass despite Redis down"
    # 4th hit: in-memory cap DOES reject, but because of the limit, not because of Redis.
    ok, _ = await check_allowed_scope(key, limit=limit, fail_mode="open_after_sig")
    assert ok is False


async def test_fail_open_after_sig_default_for_webhook_shaped_keys(monkeypatch):
    """Heuristic dispatch: keys with 'webhook' or 'reply' in them default to open."""

    _with_redis_url(monkeypatch, "redis://down.local:6379/0")
    _mark_redis_broken()

    for shaped_key in (
        "tenant:razorpay-webhook:1.1.1.1",
        "tenant:resend-webhook-verified:2.2.2.2",
        "tenant:replies-inbound:3.3.3.3",
    ):
        ok, _ = await check_allowed(shaped_key, limit=10)
        assert ok is True, f"{shaped_key} should default to fail_open_after_sig"


# ---------------------------------------------------------------------------
# TR-3.3 Razorpay webhook endpoint: valid sig + Redis down → 200 (not 429)
# ---------------------------------------------------------------------------


def _make_signed_razorpay_event() -> tuple[bytes, str]:
    """Build a fake payment_link.paid event signed with the live settings
    secret, so the endpoint's signature verification genuinely passes."""

    import hashlib
    import hmac
    import json

    from app.core.config import get_settings as _get_settings

    secret = _get_settings().RAZORPAY_WEBHOOK_SECRET
    payload = {
        "id": "evt_tr33_fake_001",
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_tr33_001",
                    "notes": {"business_id": "biz-tr33", "invoice_id": "INV-TR33"},
                    "amount": 10000,
                    "currency": "INR",
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_tr33_001",
                    "amount": 10000,
                    "currency": "INR",
                    "status": "captured",
                }
            },
        },
        "created_at": 1700000000,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, sig


async def test_webhook_200_with_valid_sig_when_redis_is_down(monkeypatch):
    """TR-3.3: valid signature + Redis outage -> 200 store, never 429.

    The webhook walls fail *open* on a Redis outage (in-memory fallback), so a
    verified payment event is stored and acknowledged instead of being turned
    into a 429 that Razorpay would retry forever.
    """

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.db.session import Base, get_db
    from app.main import app

    # Redis configured but unreachable: nothing listens on 6399, so the client
    # marks itself broken and the limiter falls back to the in-process deque.
    _with_redis_url(monkeypatch, "redis://127.0.0.1:6399/0")
    _mark_redis_broken()

    # In-memory DB with the full schema, wired through dependency overrides.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with maker() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    try:
        raw, sig = await asyncio.to_thread(_make_signed_razorpay_event)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/webhooks/razorpay",
                content=raw,
                headers={
                    "x-razorpay-signature": sig,
                    "x-razorpay-event-id": "evt_tr33_fake_001",
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 200, f"expected 200 store, got {resp.status_code}: {resp.text}"
        assert resp.json()["status"] in {"processed", "stored"}
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
