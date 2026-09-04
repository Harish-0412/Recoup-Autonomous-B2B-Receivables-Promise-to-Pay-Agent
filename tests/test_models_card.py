"""Tests for GET /models/recovery/card.

The endpoint must work on a fresh clone with no trained artifact: metrics fall
back to the committed model-card numbers, labelled as such, while the two live
flags stay honest.
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
async def test_recovery_card_returns_four_model_rows(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/recovery/card")
    assert response.status_code == 200
    data = response.json()

    assert len(data["results"]) == 4
    shipped = [row for row in data["results"] if row["shipped"]]
    assert len(shipped) == 1
    assert shipped[0]["model"] == data["shipped_model"]
    if data["source"] == "model_card_fallback":
        assert data["shipped_model"] == "xgb-recovery"


@pytest.mark.asyncio
async def test_recovery_card_head_to_head_is_internally_consistent(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/recovery/card")
    data = response.json()
    h2h = data["head_to_head"]

    assert h2h["challenger"] == data["shipped_model"]
    assert h2h["incumbent"] == "rules-based"
    assert abs(h2h["auc_delta"] - (h2h["challenger_auc"] - h2h["incumbent_auc"])) < 1e-6
    assert (
        abs(
            h2h["value_delta"]
            - (h2h["challenger_value_at_risk_at_k"] - h2h["incumbent_value_at_risk_at_k"])
        )
        < 1.0
    )
    if data["source"] == "model_card_fallback":
        # About Rs 24.6L more at-risk value in the top 20% (seed 42).
        assert h2h["challenger"] == "xgb-recovery"
        assert h2h["auc_delta"] > 0
        assert 2_000_000 < h2h["value_delta"] < 3_000_000
        assert h2h["k"] == 171


@pytest.mark.asyncio
async def test_recovery_card_carries_calibration_and_importance(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/recovery/card")
    data = response.json()

    assert len(data["calibration_bins"]) >= 4
    for b in data["calibration_bins"]:
        assert b["count"] >= 15
        # Hand-rounded fallback constants; live artifacts are exact.
        assert abs(b["gap"] - (b["predicted"] - b["observed"])) < 0.002

    assert len(data["global_importance"]) == 10
    if data["source"] == "model_card_fallback":
        assert data["global_importance"][0]["feature"] == "customer_broken_promise_rate"


@pytest.mark.asyncio
async def test_recovery_card_states_source_and_limitations(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/recovery/card")
    data = response.json()

    assert data["source"] in {"model_card.json", "evaluation_report.json", "model_card_fallback"}
    assert isinstance(data["use_model_scorer"], bool)
    assert len(data["limitations"]) >= 4
    assert any("synthetic" in line.lower() for line in data["limitations"])
