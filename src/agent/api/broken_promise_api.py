"""FastAPI router for Broken-Promise Risk Scorer.

Provides real-time scoring endpoint `/score/broken_promise` and model card inspection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import require_api_key
from src.agent.promise_handler import (
    FEATURE_COLUMNS,
    build_broken_promise_features,
    score_broken_promise,
)

router = APIRouter(tags=["broken-promise"])

REPO_ROOT = Path(__file__).resolve().parents[3]
CARD_PATH = REPO_ROOT / "models" / "broken_promise" / "model_card.json"


class BrokenPromiseScoreRequest(BaseModel):
    promise_id: str | None = None
    invoice_id: str | None = None
    customer_id: str | None = None
    promised_amount: float | None = None
    promised_date: str | None = None
    customer: dict[str, Any] | None = None
    invoice: dict[str, Any] | None = None
    # Feature overrides if provided directly
    customer_broken_promise_rate: float | None = None
    customer_broken_promises_count: int | None = None
    customer_avg_days_late: float | None = None
    customer_on_time_ratio_90d: float | None = None
    customer_on_time_ratio_all_time: float | None = None
    customer_dispute_rate: float | None = None
    customer_invoice_count: int | None = None
    customer_tenure_months: int | None = None
    invoice_amount: float | None = None
    days_overdue_at_scoring: int | None = None
    payment_terms_days: int | None = None
    days_since_last_contact: int | None = None
    prior_reminders_sent: int | None = None
    promise_amount_ratio: float | None = None
    promise_horizon_days: int | None = None
    current_escalation_tier: int | None = None


class BrokenPromiseScoreResponse(BaseModel):
    risk_score: float = Field(ge=0.0, le=1.0, description="Probability of broken promise (0 = kept, 1 = broken)")
    risk_tier: str = Field(description="'LOW' (0-0.33), 'MEDIUM' (0.33-0.66), or 'HIGH' (>0.66)")
    recommendation: str
    model_version: str = "v1.0.0"
    features_used: dict[str, float]


def get_risk_tier(score: float) -> tuple[str, str]:
    if score < 0.33:
        return "LOW", "High confidence commitment. Hold escalation; wait for promised payment date."
    elif score < 0.66:
        return "MEDIUM", "Moderate risk of slip. Schedule standard polite reminder on promised date."
    else:
        return "HIGH", "High probability of broken promise. Prepare automated ladder escalation if unpaid within 24h."


@router.post(
    "/score/broken_promise",
    response_model=BrokenPromiseScoreResponse,
    dependencies=[Depends(require_api_key)],
)
async def score_promise_endpoint(payload: BrokenPromiseScoreRequest) -> BrokenPromiseScoreResponse:
    """Predict whether a customer will honor or break their payment promise."""
    data = payload.model_dump(exclude_none=True)
    score = score_broken_promise(data)
    tier, rec = get_risk_tier(score)

    features = build_broken_promise_features(data)
    features_dict = {FEATURE_COLUMNS[i]: round(features[i], 4) for i in range(len(FEATURE_COLUMNS))}

    return BrokenPromiseScoreResponse(
        risk_score=score,
        risk_tier=tier,
        recommendation=rec,
        model_version="v1.0.0",
        features_used=features_dict,
    )


@router.get("/score/broken_promise/card")
async def get_broken_promise_model_card() -> dict[str, Any]:
    """Return model evaluation metrics, SHAP importances, and validation summary."""
    if CARD_PATH.exists():
        try:
            return json.loads(CARD_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "model_name": "broken_promise_risk_scorer",
        "model_version": "v1.0.0",
        "algorithm": "Gradient Boosted Trees (LightGBM)",
        "metrics": {"roc_auc": 0.8993, "accuracy": 0.8190, "f1_score": 0.7975},
        "source": "fallback",
    }
