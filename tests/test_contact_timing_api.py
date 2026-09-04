"""Tests for GET /schedule/next_time.

No database: ``get_db`` is overridden with a dummy session and the repository
lookup is monkeypatched. What is asserted is routing, segmentation, the
fallback contract, and the live-bandit path -- never inbox behaviour.
"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import require_api_key
from app.db.session import get_db
from app.main import app
from app.services import contact_timing
from src.ml.contact_timing.bandit import TimingBandit


def _customer(**overrides):
    fields = {
        "customer_id": "C-1",
        "invoice_count": 20,
        "on_time_ratio_90d": 0.9,
        "avg_days_late": 2.0,
        "prior_broken_promises_count": 0,
        "prior_disputes_count": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def stubbed_app(monkeypatch):
    async def _no_db():
        yield None

    async def _customer_row(session, customer_id: str):
        if customer_id == "C-MISSING":
            return None
        return _customer(customer_id=customer_id)

    from app.services import repository

    monkeypatch.setattr(repository, "get_customer", _customer_row)
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[require_api_key] = lambda: None
    contact_timing.clear_timing_cache()
    yield app
    app.dependency_overrides.clear()
    contact_timing.clear_timing_cache()


@pytest.fixture
async def async_client(stubbed_app):
    transport = ASGITransport(app=stubbed_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_next_time_unknown_customer_404(async_client: AsyncClient):
    response = await async_client.get(
        "/api/v1/schedule/next_time", params={"customer_id": "C-MISSING"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_next_time_falls_back_without_artifact(
    async_client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: None)
    response = await async_client.get(
        "/api/v1/schedule/next_time", params={"customer_id": "C-1"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["customer_id"] == "C-1"
    assert data["fallback_used"] is True
    assert data["fallback_reason"] == "model_unavailable"
    assert data["segment"] == "reliable_prompt_clean"


@pytest.mark.asyncio
async def test_next_time_serves_bandit_suggestion(
    async_client: AsyncClient, monkeypatch
):
    bandit = TimingBandit()
    bandit.fit_rows([("reliable_prompt_clean", "tue_midday", 1)] * 20)
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: (bandit, None))

    response = await async_client.get(
        "/api/v1/schedule/next_time", params={"customer_id": "C-1"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["fallback_used"] is False
    assert data["segment"] == "reliable_prompt_clean"
    # Any served arm reports its posterior mean, which is >= prior mean here.
    assert data["expected_response_rate"] >= 0.5
    assert data["observations"] >= 0
    assert "scheduled_for" in data
