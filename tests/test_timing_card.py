"""Tests for GET /models/timing/card.

No database: the endpoint serves the training script's model_card.json (or
committed fallback constants), so what is asserted is the contract -- source
labelling, lift arithmetic, and the presence of every number the studio page
renders.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_timing_card_states_source(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/timing/card")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] in {"model_card.json", "model_card_fallback"}


@pytest.mark.asyncio
async def test_timing_card_lift_is_internally_consistent(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/timing/card")
    data = response.json()

    for key in (
        "model_version",
        "policy_reward",
        "logging_reward",
        "lift_over_logging",
        "best_fixed_arm",
        "truth_mae",
    ):
        assert key in data, f"timing card missing {key}"

    assert abs(data["lift_over_logging"] - (data["policy_reward"] - data["logging_reward"])) < 1e-9
    assert 0.0 <= data["policy_reward"] <= 1.0
    assert 0.0 <= data["truth_mae"] <= 1.0
