"""Persistence for the contact-timing bandit, on the shared artifact store."""

from src.ml.artifacts import load_artifact, save_artifact
from src.ml.config import MLSettings
from src.ml.contact_timing.bandit import TimingBandit
from src.ml.schemas import ModelMetadata
from src.ml.versioning import new_model_version, utc_now

MODEL_NAME = "contact-timing-bandit"

_CACHE: dict[tuple[str, str], tuple[TimingBandit, ModelMetadata]] = {}


def save_timing_bandit(
    bandit: TimingBandit,
    *,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
    version: str | None = None,
    settings: MLSettings | None = None,
) -> tuple[object, ModelMetadata]:
    """Persist the fitted posterior with the metrics it earned."""

    resolved_version = version or new_model_version(bandit.__class__.__name__.lower())
    metadata = ModelMetadata(
        model_name=MODEL_NAME,
        model_version=resolved_version,
        trained_at=utc_now(),
        train_rows=bandit.train_rows,
        feature_columns=["segment", "arm"],
        metrics=metrics or {},
        notes=notes or "segmented Thompson Sampling, contact-timing-v1",
    )
    path = save_artifact(
        bandit,
        name=MODEL_NAME,
        version=resolved_version,
        metadata=metadata,
        settings=settings,
    )
    _CACHE.clear()
    return path, metadata


def load_timing_bandit(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
    use_cache: bool = True,
) -> tuple[TimingBandit, ModelMetadata]:
    """Load a trained bandit. Raises when no artifact exists yet."""

    key = (MODEL_NAME, version)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    bandit, metadata = load_artifact(MODEL_NAME, version=version, settings=settings)
    if not isinstance(bandit, TimingBandit):
        raise TypeError(f"Artifact {MODEL_NAME}:{version} is not a TimingBandit")

    if use_cache:
        _CACHE[key] = (bandit, metadata)
    return bandit, metadata


def clear_timing_cache() -> None:
    _CACHE.clear()


def timing_bandit_is_available(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
) -> bool:
    try:
        load_timing_bandit(version, settings=settings)
    except Exception:
        return False
    return True
