"""Tests for the Broken-Promise Risk Scorer feature.

Verifies:
1. ETL data generator produces valid, diverse mock datasets.
2. Feature builder handles varied inputs with graceful fallbacks.
3. ONNX model predicts valid probabilities with logical risk calibration.
4. FastAPI endpoints (/api/score/broken_promise and /api/score/broken_promise/card) return 200.
5. Graceful fallback on malformed input preserves system reliability.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.security import require_api_key
from app.main import app
from scripts.etl.broken_promise_etl import (
    FEATURE_COLUMNS,
    generate_mock_promise_dataset,
)
from src.agent.promise_handler import (
    build_broken_promise_features,
    get_onnx_session,
    score_broken_promise,
)


def test_mock_data_generation() -> None:
    data = generate_mock_promise_dataset(n_samples=500, seed=123)
    assert len(data) == 500

    required_keys = {"promise_id", "customer_id", *FEATURE_COLUMNS, "is_broken"}
    for row in data[:50]:
        assert required_keys.issubset(set(row.keys()))
        assert row["is_broken"] in (0, 1)
        assert 0.0 <= row["customer_on_time_ratio_90d"] <= 1.0
        assert row["promise_horizon_days"] >= 1

    broken_ratio = sum(r["is_broken"] for r in data) / len(data)
    assert 0.25 <= broken_ratio <= 0.65


def test_feature_builder_defaults() -> None:
    # Empty payload should use safe defaults and produce 19 features
    vec = build_broken_promise_features({})
    assert len(vec) == len(FEATURE_COLUMNS)
    assert all(isinstance(v, (int, float)) for v in vec)

    # Custom payload
    payload = {
        "customer_broken_promise_rate": 0.5,
        "customer_avg_days_late": 15.0,
        "days_overdue_at_scoring": 20,
        "promise_horizon_days": 10,
    }
    vec2 = build_broken_promise_features(payload)
    assert vec2[0] == 0.5
    assert vec2[2] == 15.0
    assert vec2[12] == 20.0
    assert vec2[17] == 10.0


def test_onnx_model_inference_calibration() -> None:
    session = get_onnx_session()
    assert session is not None, "ONNX model session should be initialized"

    # Low risk profile: zero broken promises, 95% on-time, 2-day horizon
    low_risk = {
        "customer_broken_promise_rate": 0.0,
        "customer_broken_promises_count": 0,
        "customer_on_time_ratio_90d": 0.95,
        "customer_on_time_ratio_all_time": 0.96,
        "days_overdue_at_scoring": 2,
        "promise_horizon_days": 2,
    }
    low_score = score_broken_promise(low_risk)
    assert 0.0 <= low_score <= 0.35, f"Expected low score, got {low_score}"

    # High risk profile: 80% broken promises, 20% on-time, 60 days overdue, 25-day horizon
    high_risk = {
        "customer_broken_promise_rate": 0.85,
        "customer_broken_promises_count": 8,
        "customer_on_time_ratio_90d": 0.15,
        "customer_on_time_ratio_all_time": 0.25,
        "days_overdue_at_scoring": 60,
        "promise_horizon_days": 25,
        "customer_dispute_rate": 0.30,
        "current_escalation_tier": 2,
    }
    high_score = score_broken_promise(high_risk)
    assert 0.65 <= high_score <= 1.0, f"Expected high score, got {high_score}"

    # Monotonicity check
    assert low_score < high_score


def test_graceful_degradation() -> None:
    # Completely invalid types must not crash, should return fallback
    score = score_broken_promise({"invalid_field": object()})
    assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_fastapi_endpoints() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # POST /api/score/broken_promise without credentials must fail with 401 or 503
        anon_res = await client.post("/api/score/broken_promise", json={
            "customer_broken_promise_rate": 0.05,
            "customer_on_time_ratio_90d": 0.92,
            "days_overdue_at_scoring": 4,
            "promise_horizon_days": 3,
        })
        assert anon_res.status_code in (401, 503)

        # GET /api/score/broken_promise/card is intentionally public
        card_res = await client.get("/api/score/broken_promise/card")
        assert card_res.status_code == 200
        card = card_res.json()
        assert card["model_name"] == "broken_promise_risk_scorer"
        assert "metrics" in card
        assert card["metrics"]["roc_auc"] > 0.85

        # Authenticated POST /api/score/broken_promise succeeds
        app.dependency_overrides[require_api_key] = lambda: None
        try:
            res = await client.post("/api/score/broken_promise", json={
                "customer_broken_promise_rate": 0.05,
                "customer_on_time_ratio_90d": 0.92,
                "days_overdue_at_scoring": 4,
                "promise_horizon_days": 3,
            })
            assert res.status_code == 200
            body = res.json()
            assert "risk_score" in body
            assert body["risk_tier"] in ("LOW", "MEDIUM", "HIGH")
            assert "recommendation" in body
            assert body["model_version"] == "v1.0.0"
            assert len(body["features_used"]) == 19
        finally:
            app.dependency_overrides.pop(require_api_key, None)
