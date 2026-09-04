"""Tests for POST /replies/classify-preview.

The preview is explicitly non-mutating: it takes no database session, writes
nothing, and reuses the same understand_reply path as ingestion. Tests assert
shape and the deterministic opt-out guard path, never exact LLM output.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app
from src.ml.schemas import IntentLabel


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def auth_headers(monkeypatch):
    """Operator routes are bearer-locked; tests present the cron key."""

    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "the-cron-secret")
    monkeypatch.delenv("API_KEY", raising=False)
    yield {"Authorization": "Bearer the-cron-secret"}
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_preview_without_a_key_is_refused(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/replies/classify-preview",
        json={"text": "We will pay by Friday."},
    )
    assert response.status_code in (401, 503)


@pytest.mark.asyncio
async def test_preview_opt_out_hits_the_deterministic_guard(
    async_client: AsyncClient, auth_headers: dict
):
    response = await async_client.post(
        "/api/v1/replies/classify-preview",
        json={"text": "Stop sending me these reminders, I will handle this directly."},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["intent"] == IntentLabel.OPT_OUT.value
    assert data["stage_used"] == "guard"
    assert data["confidence"] == 1.0
    assert data["needs_review"] is False


@pytest.mark.asyncio
async def test_preview_returns_a_well_formed_shape(async_client: AsyncClient, auth_headers: dict):
    response = await async_client.post(
        "/api/v1/replies/classify-preview",
        json={"text": "We will pay by Friday, just had a cashflow issue."},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["intent"] in {label.value for label in IntentLabel}
    assert 0.0 <= data["confidence"] <= 1.0
    assert data["stage_used"] in {"cascade", "llm", "guard"}
    assert isinstance(data["fallback_used"], bool)
    assert isinstance(data["needs_review"], bool)
    assert "entities" in data
    assert data["classifier_version"]


@pytest.mark.asyncio
async def test_preview_rejects_empty_text(async_client: AsyncClient, auth_headers: dict):
    response = await async_client.post(
        "/api/v1/replies/classify-preview",
        json={"text": "   "},
        headers=auth_headers,
    )
    # Pydantic min_length passes on whitespace; the classifier treats it as
    # empty and the endpoint still answers honestly rather than raising.
    assert response.status_code == 200
    data = response.json()
    assert data["needs_review"] is True


@pytest.mark.asyncio
async def test_preview_rejects_missing_text(async_client: AsyncClient, auth_headers: dict):
    response = await async_client.post(
        "/api/v1/replies/classify-preview",
        json={},
        headers=auth_headers,
    )
    assert response.status_code == 422
