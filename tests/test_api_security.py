"""The HTTP perimeter: bearer gates, no backdoors, and rate limits.

These are the properties the hardcoded-key regression broke, so they are
asserted at the layer that regressed (route dependencies through a real
ASGI app) rather than only against the helper in isolation:

* every operator route answers 401 without a key -- including
  ``POST /invoices/{id}/run-cycle``;
* a publishable provider ID (``RAZORPAY_KEY_ID``) or a formerly-hardcoded
  dev string is NOT a credential;
* an unconfigured deploy answers 503, never 200;
* the ingest + webhook rate limiter degrades with 429 + Retry-After.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.ratelimit import check_allowed, clear
from app.core.security import require_api_key, require_task_key


@pytest.fixture
async def async_client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def operator_key(monkeypatch):
    """A configured deploy with distinct cron and dashboard keys."""
    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "the-cron-secret")
    monkeypatch.setenv("API_KEY", "the-operator-secret")
    yield ("the-cron-secret", "the-operator-secret")
    get_settings.cache_clear()


@pytest.fixture
def cron_only_key(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "the-cron-secret")
    monkeypatch.delenv("API_KEY", raising=False)
    yield "the-cron-secret"
    get_settings.cache_clear()


# --- the backdoor stays closed ------------------------------------------------


def test_task_key_accepts_only_the_configured_cron_key(operator_key):
    cron, _ = operator_key
    require_task_key(f"Bearer {cron}")  # must not raise


@pytest.mark.parametrize(
    "token",
    [
        "rzp_test_TXVM95IjmdDy4d",  # publishable Razorpay ID is not a credential
        "recoup_dev_task_key_2026",  # formerly hardcoded dev string
        "the-operator-secret",  # dashboard key does not open task endpoints
        "",
        "wrong",
    ],
)
def test_task_key_refuses_non_credentials(operator_key, token):
    with pytest.raises(HTTPException) as caught:
        require_task_key(f"Bearer {token}")
    assert caught.value.status_code == 401


def test_razorpay_key_id_configured_as_key_id_still_denies(monkeypatch):
    """Even the *live* RAZORPAY_KEY_ID must never authenticate."""

    from app.core.config import Settings

    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "the-cron-secret")
    monkeypatch.delenv("API_KEY", raising=False)
    try:
        live_key_id = Settings(_env_file=None).RAZORPAY_KEY_ID
        assert live_key_id  # guard: the test is vacuous if the env has none
        with pytest.raises(HTTPException) as caught:
            require_task_key(f"Bearer {live_key_id}")
        assert caught.value.status_code == 401
    finally:
        get_settings.cache_clear()


def test_api_key_falls_back_to_cron_key(cron_only_key):
    require_api_key(f"Bearer {cron_only_key}")  # must not raise


def test_api_key_prefers_but_does_not_require_api_key(operator_key):
    _, operator = operator_key
    require_api_key(f"Bearer {operator}")  # must not raise


def test_unconfigured_api_key_denies_rather_than_allows(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setenv("TASK_API_KEY", "")
    monkeypatch.setenv("API_KEY", "")
    # _env_file=None is load-bearing: pydantic-settings lets a local .env
    # win over an *empty* env var, so a developer's .env key would leak into
    # this assertion and turn the expected 503 into a 401.
    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: cfg.Settings(_env_file=None, APP_ENV="development"),
    )
    with pytest.raises(HTTPException) as caught:
        require_api_key("Bearer anything")
    assert caught.value.status_code == 503


# --- the perimeter, over HTTP ---------------------------------------------------

# Operator routes: no key -> 401 (or 503 when unconfigured), never 200/404.
PROTECTED = [
    ("GET", "/api/v1/invoices/INV-1"),
    ("GET", "/api/v1/invoices/INV-1/audit"),
    ("POST", "/api/v1/invoices/INV-1/run-cycle"),
    ("GET", "/api/v1/invoices?page=1"),
    ("POST", "/api/v1/invoices/batch"),
    ("GET", "/api/v1/reports/batch"),
    ("GET", "/api/v1/policy"),
    ("POST", "/api/v1/policy/simulate"),
    ("GET", "/api/v1/replies/review"),
    ("POST", "/api/v1/replies/r1/reviewed"),
    ("POST", "/api/v1/replies/classify-preview"),
    ("GET", "/api/v1/schedule/next_time?customer_id=C-1"),
    ("POST", "/score/broken_promise"),
    ("POST", "/api/score/broken_promise"),
    ("POST", "/api/v1/score/broken_promise"),
    ("GET", "/api/v1/forecast/cash"),
    ("GET", "/api/v1/drift/flags"),
]


@pytest.mark.asyncio
async def test_protected_routes_reject_anonymous(async_client, operator_key):
    for method, path in PROTECTED:
        response = await async_client.request(method, path)
        assert response.status_code in (401, 503), f"{method} {path}"
    clear()


@pytest.mark.asyncio
async def test_protected_routes_reject_a_forged_bearer(async_client, operator_key):
    headers = {"Authorization": "Bearer rzp_test_TXVM95IjmdDy4d"}
    for method, path in PROTECTED:
        response = await async_client.request(method, path, headers=headers)
        assert response.status_code == 401, f"{method} {path}"
    clear()


@pytest.mark.asyncio
async def test_operator_key_opens_a_db_free_route(async_client, operator_key):
    """GET /policy touches no database, so 200 proves auth passed."""

    _, operator = operator_key
    response = await async_client.get(
        "/api/v1/policy", headers={"Authorization": f"Bearer {operator}"}
    )
    assert response.status_code == 200
    assert "rule_count" in response.json()


@pytest.mark.asyncio
async def test_health_stays_public(async_client, operator_key):
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200


PUBLIC_MODEL_CARDS = [
    "/api/v1/models/recovery/card",
    "/api/v1/models/drift/card",
    "/api/v1/models/timing/card",
    "/score/broken_promise/card",
    "/api/score/broken_promise/card",
    "/api/v1/score/broken_promise/card",
]


@pytest.mark.asyncio
async def test_model_cards_stay_public(async_client, operator_key):
    """Model cards are public evidence and must answer 200 without bearer tokens."""
    for path in PUBLIC_MODEL_CARDS:
        response = await async_client.get(path)
        assert response.status_code == 200, f"GET {path} failed with {response.status_code}"


# --- rate limits -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sliding_window_allows_burst_then_denies_with_retry_after():
    await clear()
    key = "test-scope:127.0.0.1"
    for _ in range(5):
        allowed, _ = await check_allowed(key, limit=5)
        assert allowed is True
    allowed, retry_after = await check_allowed(key, limit=5)
    assert allowed is False
    assert retry_after > 0
    await clear()
    allowed, _ = await check_allowed(key, limit=5)
    assert allowed is True
    await clear()


@pytest.mark.asyncio
async def test_buckets_are_scoped_per_key():
    await clear()
    allowed_a, _ = await check_allowed("scope:1.1.1.1", limit=1)
    allowed_b, _ = await check_allowed("scope:2.2.2.2", limit=1)
    assert (allowed_a, allowed_b) == (True, True)
    denied, _ = await check_allowed("scope:1.1.1.1", limit=1)
    assert denied is False
    await clear()
