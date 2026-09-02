from pathlib import Path

from src.ml import MLSettings, new_model_version, utc_now_iso
from src.ml.artifacts import load_artifact, save_artifact
from src.ml.schemas import ModelMetadata
from src.ml.versioning import utc_now


def test_import_src_ml_succeeds():
    import src.ml as ml

    assert ml.MLSettings is MLSettings


def test_ml_settings_defaults():
    settings = MLSettings()

    assert settings.ml_artifacts_dir == Path("./src/ml/artifacts_store")
    assert settings.ml_random_seed == 42
    assert settings.ml_confidence_threshold == 0.6
    assert settings.recovery_horizon_days == 30


def test_artifacts_dir_is_created(tmp_path):
    settings = MLSettings(ml_artifacts_dir=tmp_path / "artifacts")

    created = settings.ensure_artifacts_dir()

    assert created.exists()
    assert created.is_dir()


def test_new_model_version_shape():
    version = new_model_version("Recovery XGB")

    assert version.startswith("recovery-xgb-")
    assert len(version.rsplit("-", maxsplit=1)[-1]) == 6


def test_utc_now_iso_uses_utc_suffix():
    assert utc_now_iso().endswith("Z")


def test_save_load_artifact_round_trip(tmp_path):
    settings = MLSettings(ml_artifacts_dir=tmp_path / "artifacts")
    metadata = ModelMetadata(
        model_name="dummy-model",
        model_version="dummy-model-v1",
        trained_at=utc_now(),
        train_rows=2,
        feature_columns=["a", "b"],
        metrics={"auc": 0.75},
        notes="phase 0 smoke test",
    )
    original = {"weights": [1, 2, 3], "bias": 0.25}

    saved_dir = save_artifact(
        original,
        name="dummy-model",
        version="dummy-model-v1",
        metadata=metadata,
        settings=settings,
    )
    loaded_obj, loaded_metadata = load_artifact(
        "dummy-model",
        version="latest",
        settings=settings,
    )

    assert saved_dir.exists()
    assert loaded_obj == original
    assert loaded_metadata == metadata
