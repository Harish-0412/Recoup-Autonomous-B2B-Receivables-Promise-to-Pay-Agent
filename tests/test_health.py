import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

REDIS_URL = os.environ.get("RATE_LIMIT_REDIS_URL")
HAS_REDIS = bool(REDIS_URL)


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def _clear_settings_cache(monkeypatch):
    """Point get_settings at a URL the test chooses, then restore."""

    def _set(url: str) -> None:
        import app.core.config as cfg

        monkeypatch.setenv("RATE_LIMIT_REDIS_URL", url)
        cfg.get_settings.cache_clear()

    return _set


@pytest.fixture
def _reset_limiter():
    """Restore the module-level Redis client state after a down-Redis test."""

    yield
    import app.core.ratelimit as ratelimit

    ratelimit._redis_client = None
    ratelimit._redis_client_broken = False
    ratelimit._redis_client_last_try = 0.0


@pytest.mark.asyncio
async def test_health_check(async_client: AsyncClient):
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "Recoup"


@pytest.mark.asyncio
async def test_ready_without_redis_url_skips_redis(
    async_client: AsyncClient, _clear_settings_cache, _reset_limiter
):
    """TR-4.1: no RATE_LIMIT_REDIS_URL -> 200 with ``redis == skipped``."""

    _clear_settings_cache("")
    response = await async_client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "skipped"


@pytest.mark.asyncio
async def test_ready_503_when_redis_configured_but_down(
    async_client: AsyncClient, _clear_settings_cache, _reset_limiter
):
    """TR-4.3: URL set but Redis unreachable -> 503 with ``redis == fail``."""

    _clear_settings_cache("redis://127.0.0.1:6399/0")
    response = await async_client.get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "fail"
    assert body["failing"] == ["redis"]


@pytest.mark.skipif(not HAS_REDIS, reason="RATE_LIMIT_REDIS_URL not set")
@pytest.mark.asyncio
async def test_ready_with_healthy_redis(
    async_client: AsyncClient, _clear_settings_cache, _reset_limiter
):
    """TR-4.2: live Redis container -> 200 with ``redis == ok``."""

    _clear_settings_cache(REDIS_URL or "")
    response = await async_client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["redis"] == "ok"


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient):
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Recoup"
    assert data["version"] == "0.1.0"
