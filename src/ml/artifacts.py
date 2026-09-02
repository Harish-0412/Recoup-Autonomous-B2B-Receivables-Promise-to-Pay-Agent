"""Save and load versioned ML artifacts."""

from pathlib import Path
from typing import Any

import joblib

from src.ml.config import MLSettings
from src.ml.schemas import ModelMetadata

MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "meta.json"
LATEST_FILENAME = "latest.txt"


def _safe_segment(value: str, label: str) -> str:
    if not value or value.strip() != value:
        raise ValueError(f"{label} must be non-empty and must not have outer whitespace")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a single path segment")
    return value


def _artifact_root(settings: MLSettings | None = None) -> Path:
    active_settings = settings or MLSettings()
    return active_settings.ensure_artifacts_dir()


def save_artifact(
    obj: Any,
    name: str,
    version: str,
    metadata: ModelMetadata,
    settings: MLSettings | None = None,
) -> Path:
    """Persist a model object and its metadata under a versioned directory."""

    artifact_name = _safe_segment(name, "name")
    artifact_version = _safe_segment(version, "version")

    if metadata.model_name != artifact_name:
        raise ValueError("metadata.model_name must match artifact name")
    if metadata.model_version != artifact_version:
        raise ValueError("metadata.model_version must match artifact version")

    root = _artifact_root(settings)
    version_dir = root / artifact_name / artifact_version
    version_dir.mkdir(parents=True, exist_ok=True)

    joblib.dump(obj, version_dir / MODEL_FILENAME)
    (version_dir / METADATA_FILENAME).write_text(
        metadata.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (root / artifact_name / LATEST_FILENAME).write_text(artifact_version, encoding="utf-8")
    return version_dir


def load_artifact(
    name: str,
    version: str = "latest",
    settings: MLSettings | None = None,
) -> tuple[Any, ModelMetadata]:
    """Load a previously saved model object and its metadata."""

    artifact_name = _safe_segment(name, "name")
    root = _artifact_root(settings)
    artifact_root = root / artifact_name

    if version == "latest":
        latest_path = artifact_root / LATEST_FILENAME
        if not latest_path.exists():
            raise FileNotFoundError(f"No latest artifact pointer found for {artifact_name}")
        resolved_version = latest_path.read_text(encoding="utf-8").strip()
    else:
        resolved_version = _safe_segment(version, "version")

    version_dir = artifact_root / _safe_segment(resolved_version, "version")
    model_path = version_dir / MODEL_FILENAME
    metadata_path = version_dir / METADATA_FILENAME

    if not model_path.exists():
        raise FileNotFoundError(f"Model file not found: {model_path}")
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")

    model_obj = joblib.load(model_path)
    metadata = ModelMetadata.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    return model_obj, metadata
