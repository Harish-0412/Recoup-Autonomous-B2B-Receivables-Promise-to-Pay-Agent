"""Probabilistic cash forecast over the current at-risk receivables.

Read-only and dry by construction: scoring and simulation touch no state,
send nothing, and advance no ladder rung. Refreshing this endpoint is as
safe as refreshing the batch report.

The forecast multiplies two honest statements per invoice -- P(pays within
the horizon) from the recovery scorer and, conditional on payment, an
empirical payment-lag distribution per observable customer segment -- over
10k Monte Carlo futures (tunable). The lag tables and the frozen probability
calibration come from the versioned cash-forecast artifact; with no trained
artifact the endpoint answers 503 rather than inventing lags, because a
forecast with a made-up timing leg is worse than no forecast.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import require_api_key
from app.db.session import get_db
from app.schemas.cash_forecast import CashForecastOut, WindowForecastOut
from app.services import repository
from src.ml.cash_forecast.simulate import DEFAULT_DRAWS, MAX_DRAWS, MIN_DRAWS, WINDOWS

router = APIRouter(prefix="/forecast", tags=["forecast"], dependencies=[Depends(require_api_key)])
logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
CARD_DIR = REPO_ROOT / "data" / "cash_forecast"


class ForecastUnavailable(Exception):
    """The forecast cannot be served; the detail is operator-facing."""


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


@lru_cache(maxsize=1)
def load_forecast_model() -> tuple[Any, Any]:
    """Load the fitted lag tables + calibration, once per process.

    ``lru_cache`` because artifact load is disk IO that must not happen per
    request; ``clear_forecast_cache`` in tests resets it. Raises
    ``ForecastUnavailable`` with an actionable message instead of leaking a
    FileNotFoundError to the operator.
    """

    try:
        from src.ml.cash_forecast.artifacts import load_cash_forecast_model
    except Exception as exc:
        raise ForecastUnavailable(
            "Cash-forecast ML dependencies are not installed; "
            "install the ml extra to serve this endpoint."
        ) from exc
    try:
        return load_cash_forecast_model()
    except Exception as exc:
        raise ForecastUnavailable(
            "No trained cash-forecast artifact. Run " "scripts/train_cash_forecast.py, then retry."
        ) from exc


def clear_forecast_cache() -> None:
    """Reset the process-level artifact cache (tests only)."""

    clear = getattr(load_forecast_model, "cache_clear", None)
    if callable(clear):
        clear()
    try:
        from src.ml.cash_forecast.artifacts import clear_cash_forecast_cache

        clear_cash_forecast_cache()
    except Exception:  # pragma: no cover - environment without ML extras
        pass


def score_book_probabilities(cases: list[Any]) -> tuple[list[float], str, int]:
    """P(recovery) for every case, batching the model path past SHAP.

    Returns (probabilities, scorer_version, fallback_count). The forecast
    needs no per-prediction drivers, so the trained model scores one
    predict_proba call instead of thousands of SHAP explanations. The rules
    path is per-case but cheap. With no ML extras at all, the hand-written
    scorer in app.core answers directly -- the one branch needing no ML
    import, mirroring app.core.agent's own degradation.
    """

    if not cases:
        return [], "rules-based", 0

    try:
        from src.ml.recovery.scorer import ModelBasedScorer, get_recovery_scorer

        scorer = get_recovery_scorer()
    except Exception:
        scorer = None

    if scorer is None:
        from app.core.scorer import estimate_recovery_probability

        probs = [float(estimate_recovery_probability(case)[0]) for case in cases]
        return probs, "rules-based", len(cases)

    if isinstance(scorer, ModelBasedScorer) and scorer.is_available and scorer.model is not None:
        from src.ml.recovery.dataset import features_frame

        frame = features_frame(cases)
        probs = [float(p) for p in scorer.model.predict_proba(frame)]
        version = (
            scorer.metadata.model_version if scorer.metadata is not None else scorer.model.name
        )
        return probs, version, 0

    scored = [scorer.score(case) for case in cases]
    fallbacks = sum(1 for prediction in scored if prediction.fallback_used)
    declared = getattr(scorer, "name", "")
    version = declared if isinstance(declared, str) and declared else type(scorer).__name__
    return [float(prediction.p_recovery_30d) for prediction in scored], version, fallbacks


@router.get("/cash", response_model=CashForecastOut)
async def cash_forecast(
    limit: int = Query(default=200, ge=1, le=500),
    draws: int = Query(default=DEFAULT_DRAWS, ge=MIN_DRAWS, le=MAX_DRAWS),
    seed: int = Query(default=0),
    db: AsyncSession = Depends(get_db),
) -> CashForecastOut:
    """Forecasted cash landing in 7-day and 30-day windows, with intervals."""

    try:
        model, metadata = load_forecast_model()
    except ForecastUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    from src.ml.cash_forecast.lags import segment_for_case
    from src.ml.cash_forecast.simulate import ForecastInput, forecast_cash
    from src.ml.versioning import utc_now

    invoices = await repository.list_open_invoices(db, limit=limit)
    cases = [await repository.load_case(db, invoice) for invoice in invoices]
    actionable = [case for case in cases if case.invoice.outstanding > 0]

    probs, scorer_version, fallbacks = score_book_probabilities(actionable)
    inputs = [
        ForecastInput(
            invoice_id=case.invoice.invoice_id,
            amount=case.invoice.outstanding,
            p_recovery=model.scaler.calibrate(prob),
            segment=segment_for_case(case),
        )
        for case, prob in zip(actionable, probs, strict=True)
    ]

    forecast = forecast_cash(inputs, model.lags, windows=WINDOWS, draws=draws, seed=seed)
    logger.info(
        "Cash forecast served",
        invoices=forecast.n_invoices,
        draws=draws,
        scorer=scorer_version,
        lag_model=metadata.model_version,
    )
    window_outs = []
    for window in WINDOWS:
        stats = forecast.windows[window]
        window_outs.append(
            WindowForecastOut(
                window_days=stats.window_days,
                draws=stats.draws,
                mean=stats.mean,
                median=stats.median,
                p5=stats.p5,
                p25=stats.p25,
                p75=stats.p75,
                p95=stats.p95,
                prob_any_cash=stats.prob_any_cash,
            )
        )
    return CashForecastOut(
        windows=window_outs,
        n_invoices=forecast.n_invoices,
        at_risk_value=forecast.at_risk_value,
        draws=forecast.draws,
        seed=forecast.seed,
        probability_source=scorer_version,
        scorer_fallbacks=fallbacks,
        lag_model_version=metadata.model_version,
        calibrated=abs(model.scaler.slope - 1.0) > 1e-9 or abs(model.scaler.intercept) > 1e-9,
        generated_at=utc_now(),
    )


@router.get("/cash/card")
async def cash_forecast_card() -> dict[str, Any]:
    """Validation numbers behind the forecast: coverage, bias, Platt coeffs.

    Served from the training script's outputs -- ``model_card.json`` first,
    then the full ``evaluation_report.json`` -- so the API never recomputes
    them. With neither present the card says the model is untrained rather
    than presenting fallback numbers as validation.
    """

    card = _read_json(CARD_DIR / "model_card.json")
    if card is not None:
        return {"source": "model_card.json", **card}
    report = _read_json(CARD_DIR / "evaluation_report.json")
    if report is not None:
        return {"source": "evaluation_report.json", **report}
    return {
        "model_name": "cash-forecast-model",
        "source": "not_trained",
        "detail": "Run scripts/train_cash_forecast.py to train and validate the model.",
    }
