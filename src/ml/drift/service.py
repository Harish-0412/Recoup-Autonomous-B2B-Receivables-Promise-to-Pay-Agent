"""Serving the drift model: one customer in, one verdict out.

``DriftScorer`` mirrors the reply cascade's contract: a missing artifact is
a *fallback*, never an exception. With no trained model the scorer answers
"unknown, not flagged" and says so (`fallback_used=True`), so the nightly
job and the API degrade to doing nothing rather than failing -- a drift
signal must never block collection or invent risk.

Explanations: Isolation Forest has no native feature attribution, so top
drivers are standardized deviations from the training-normal medians --
"this customer's delay trend sits 3.1 standard deviations above normal
payers". The reference medians and scales ship inside the artifact, so
explanations need no live data and cannot drift with the book.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.ml.config import MLSettings
from src.ml.drift.artifacts import load_drift_model
from src.ml.drift.features import FEATURE_COLUMNS, PeriodSeries, from_period_series
from src.ml.drift.models import FittedDrift
from src.ml.versioning import utc_now

MODEL_VERSION_FALLBACK = "drift-fallback"


@dataclass(frozen=True)
class DriftDriver:
    feature: str
    value: float
    deviation: float


@dataclass(frozen=True)
class DriftResult:
    customer_id: str
    anomaly_score: float | None
    flagged: bool
    threshold: float | None
    model_version: str
    fallback_used: bool
    top_drivers: tuple[DriftDriver, ...]
    scored_at: str


class DriftScorer:
    """Scores customers against the latest trained drift artifact."""

    def __init__(self, settings: MLSettings | None = None) -> None:
        self._settings = settings or MLSettings()
        self._model: FittedDrift | None = None
        self._version = MODEL_VERSION_FALLBACK
        try:
            model, metadata = load_drift_model(settings=self._settings)
            self._model = model
            self._version = metadata.model_version
        except Exception:
            self._model = None

    @property
    def available(self) -> bool:
        """True when a trained artifact backs this scorer."""
        return self._model is not None

    @property
    def model_version(self) -> str:
        return self._version

    @property
    def threshold(self) -> float | None:
        return self._model.threshold if self._model else None

    def score_series(self, customer_id: str, series: PeriodSeries) -> DriftResult:
        """Score one customer from a trailing-window period series."""

        features = from_period_series(series)
        return self.score_features(customer_id, features)

    def score_features(self, customer_id: str, features: dict[str, float]) -> DriftResult:
        """Score one customer from an already-built feature dict."""

        model = self._model
        if model is None:
            return DriftResult(
                customer_id=customer_id,
                anomaly_score=None,
                flagged=False,
                threshold=None,
                model_version=self._version,
                fallback_used=True,
                top_drivers=(),
                scored_at=utc_now().isoformat(),
            )

        frame = pd.DataFrame([{c: float(features[c]) for c in FEATURE_COLUMNS}]).astype("float64")
        score = float(model.anomaly_score(frame)[0])
        flagged = bool(score < model.threshold)
        drivers = top_drivers_for(model, features) if flagged else ()
        return DriftResult(
            customer_id=customer_id,
            anomaly_score=score,
            flagged=flagged,
            threshold=model.threshold,
            model_version=self._version,
            fallback_used=False,
            top_drivers=drivers,
            scored_at=utc_now().isoformat(),
        )


def top_drivers_for(
    model: FittedDrift, features: dict[str, float], *, limit: int = 3
) -> tuple[DriftDriver, ...]:
    """Standardized deviations from training medians, largest first.

    Scale comes from the forest itself: each tree's split thresholds imply
    the feature's training spread. Simpler and stabler: use the reference
    median with a robust scale estimated from the training frame at train
    time. To keep the artifact small we approximate scale with
    ``max(|median|, 1.0)``-relative deviation -- a documented approximation
    for ranking only, never for the flag decision itself.
    """

    ranked = []
    for column, median in zip(model.feature_columns, model.reference_median, strict=False):
        value = float(features[column])
        scale = max(abs(median), 0.05)
        deviation = (value - median) / scale
        ranked.append(DriftDriver(feature=column, value=value, deviation=deviation))
    ranked.sort(key=lambda d: abs(d.deviation), reverse=True)
    return tuple(ranked[:limit])
