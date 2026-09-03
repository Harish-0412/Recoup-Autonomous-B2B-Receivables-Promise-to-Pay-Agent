"""Persistence for the recovery model, on the shared versioned artifact store."""

from pathlib import Path

from src.ml.artifacts import load_artifact, save_artifact
from src.ml.config import MLSettings
from src.ml.features.recovery_features import FEATURE_COLUMNS_V1, FEATURE_SET_VERSION
from src.ml.recovery.models import FittedModel
from src.ml.schemas import ModelMetadata
from src.ml.versioning import new_model_version, utc_now

MODEL_NAME = "recovery-model"

_CACHE: dict[tuple[str, str], tuple[FittedModel, ModelMetadata]] = {}


def save_recovery_model(
    model: FittedModel,
    *,
    train_rows: int,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
    version: str | None = None,
    settings: MLSettings | None = None,
) -> tuple[Path, ModelMetadata]:
    """Persist the fitted model with the feature set it was trained against.

    ``feature_columns`` records the exact schema, so a later feature-set change
    is detectable at load time instead of showing up as quietly wrong scores.
    """

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


def load_recovery_model(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
    use_cache: bool = True,
) -> tuple[FittedModel, ModelMetadata]:
    """Load a trained recovery model, checking its feature schema still matches."""

    key = (MODEL_NAME, version)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    model, metadata = load_artifact(MODEL_NAME, version=version, settings=settings)
    if not isinstance(model, FittedModel):
        raise TypeError(f"Artifact {MODEL_NAME}:{version} is not a FittedModel")

    if tuple(metadata.feature_columns) != tuple(FEATURE_COLUMNS_V1):
        raise ValueError(
            f"Model {metadata.model_version} was trained on a different feature set "
            f"({len(metadata.feature_columns)} columns) than this code builds "
            f"({len(FEATURE_COLUMNS_V1)}). Retrain, or load a matching version."
        )

    if use_cache:
        _CACHE[key] = (model, metadata)
    return model, metadata


def clear_recovery_cache() -> None:
    _CACHE.clear()


def recovery_model_is_available(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
) -> bool:
    try:
        load_recovery_model(version, settings=settings)
    except Exception:
        return False
    return True
