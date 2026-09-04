"""Persistence for the drift model, on the shared versioned artifact store."""

from pathlib import Path

from src.ml.artifacts import load_artifact, save_artifact
from src.ml.config import MLSettings
from src.ml.drift.features import FEATURE_COLUMNS, FEATURE_SET_VERSION
from src.ml.drift.models import MODEL_NAME, FittedDrift
from src.ml.schemas import ModelMetadata
from src.ml.versioning import new_model_version, utc_now

_CACHE: dict[tuple[str, str], tuple[FittedDrift, ModelMetadata]] = {}


def save_drift_model(
    model: FittedDrift,
    *,
    train_rows: int,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
    version: str | None = None,
    settings: MLSettings | None = None,
) -> tuple[Path, ModelMetadata]:
    """Persist the fitted forest with the feature set it was trained against."""

    resolved_version = version or new_model_version(model.name)
    metadata = ModelMetadata(
        model_name=MODEL_NAME,
        model_version=resolved_version,
        trained_at=utc_now(),
        train_rows=train_rows,
        feature_columns=list(model.feature_columns),
        metrics=metrics or {},
        notes=notes or f"feature set {FEATURE_SET_VERSION}",
    )
    path = save_artifact(
        model,
        name=MODEL_NAME,
        version=resolved_version,
        metadata=metadata,
        settings=settings,
    )
    _CACHE.clear()
    return path, metadata


def load_drift_model(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
    use_cache: bool = True,
) -> tuple[FittedDrift, ModelMetadata]:
    """Load a trained drift model, checking its feature schema still matches."""

    key = (MODEL_NAME, version)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    model, metadata = load_artifact(MODEL_NAME, version=version, settings=settings)
    if not isinstance(model, FittedDrift):
        raise TypeError(f"Artifact {MODEL_NAME}:{version} is not a FittedDrift")

    if tuple(metadata.feature_columns) != tuple(FEATURE_COLUMNS):
        raise ValueError(
            f"Model {metadata.model_version} was trained on a different feature set "
            f"({len(metadata.feature_columns)} columns) than this code builds "
            f"({len(FEATURE_COLUMNS)}). Retrain, or load a matching version."
        )

    if use_cache:
        _CACHE[key] = (model, metadata)
    return model, metadata


def clear_drift_cache() -> None:
    _CACHE.clear()


def drift_model_is_available(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
) -> bool:
    try:
        load_drift_model(version, settings=settings)
    except Exception:
        return False
    return True
