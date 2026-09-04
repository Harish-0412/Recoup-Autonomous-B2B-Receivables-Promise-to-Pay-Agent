"""Persistence for the cash-forecast model, on the shared versioned store.

What is stored is deliberately boring: per-segment sorted lag lists, the
pooled global list, the frozen Platt coefficients, and the training
provenance. No estimator objects, no closures -- the artifact is inspectable
JSON-equivalent data, and loading it cannot execute anything but joblib's
deserialisation of plain containers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from src.ml.artifacts import load_artifact, save_artifact
from src.ml.cash_forecast.backtest import PlattScaler
from src.ml.cash_forecast.lags import LagTables
from src.ml.config import MLSettings
from src.ml.schemas import ModelMetadata
from src.ml.versioning import new_model_version, utc_now

MODEL_NAME = "cash-forecast-model"

_CACHE: dict[tuple[str, str], tuple[CashForecastModel, ModelMetadata]] = {}


@dataclass(frozen=True)
class CashForecastModel:
    """Everything the serving path needs: lags + frozen calibration."""

    lags: LagTables
    scaler: PlattScaler
    scaler_fitted_rows: int = 0
    min_segment_samples: int = 30


@dataclass
class CashForecastPayload:
    """joblib-safe envelope (plain containers only)."""

    segments: dict[str, list[int]] = field(default_factory=dict)
    global_lags: list[int] = field(default_factory=list)
    pooled_segments: list[str] = field(default_factory=list)
    min_segment_samples: int = 30
    horizon_days: int = 30
    train_rows: int = 0
    seed: int = 0
    scaler_slope: float = 1.0
    scaler_intercept: float = 0.0
    scaler_fitted_rows: int = 0

    def to_model(self) -> CashForecastModel:
        """Rehydrate the serving object. Validates shape on the way in."""

        if not self.global_lags:
            raise ValueError("cash-forecast artifact has no global lags")
        return CashForecastModel(
            lags=LagTables(
                segments={k: tuple(v) for k, v in self.segments.items()},
                global_lags=tuple(self.global_lags),
                pooled_segments=tuple(self.pooled_segments),
                min_segment_samples=self.min_segment_samples,
                horizon_days=self.horizon_days,
                train_rows=self.train_rows,
                seed=self.seed,
            ),
            scaler=PlattScaler(slope=self.scaler_slope, intercept=self.scaler_intercept),
            scaler_fitted_rows=self.scaler_fitted_rows,
            min_segment_samples=self.min_segment_samples,
        )


def payload_from_model(model: CashForecastModel) -> CashForecastPayload:
    """Flatten the serving object into the joblib-safe envelope."""

    return CashForecastPayload(
        segments={k: list(v) for k, v in model.lags.segments.items()},
        global_lags=list(model.lags.global_lags),
        pooled_segments=list(model.lags.pooled_segments),
        min_segment_samples=model.min_segment_samples,
        horizon_days=model.lags.horizon_days,
        train_rows=model.lags.train_rows,
        seed=model.lags.seed,
        scaler_slope=model.scaler.slope,
        scaler_intercept=model.scaler.intercept,
        scaler_fitted_rows=model.scaler_fitted_rows,
    )


def save_cash_forecast_model(
    model: CashForecastModel,
    *,
    train_rows: int,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
    version: str | None = None,
    settings: MLSettings | None = None,
) -> tuple[object, ModelMetadata]:
    """Persist the fitted tables + calibration under a new version."""

    resolved_version = version or new_model_version(MODEL_NAME)
    metadata = ModelMetadata(
        model_name=MODEL_NAME,
        model_version=resolved_version,
        trained_at=utc_now(),
        train_rows=train_rows,
        feature_columns=["amount", "p_recovery", "segment"],
        metrics=metrics or {},
        notes=notes or "empirical per-segment payment lags + Platt-scaled recovery probabilities",
    )
    path = save_artifact(
        asdict(payload_from_model(model)),
        name=MODEL_NAME,
        version=resolved_version,
        metadata=metadata,
        settings=settings,
    )
    _CACHE.clear()
    return path, metadata


def load_cash_forecast_model(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
    use_cache: bool = True,
) -> tuple[CashForecastModel, ModelMetadata]:
    """Load the fitted tables + calibration, validating shape on the way in."""

    key = (MODEL_NAME, version)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    payload_dict, metadata = load_artifact(MODEL_NAME, version=version, settings=settings)
    payload = CashForecastPayload(**payload_dict)
    model = payload.to_model()
    if use_cache:
        _CACHE[key] = (model, metadata)
    return model, metadata


def clear_cash_forecast_cache() -> None:
    _CACHE.clear()


def cash_forecast_model_is_available(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
) -> bool:
    try:
        load_cash_forecast_model(version, settings=settings)
    except Exception:
        return False
    return True
