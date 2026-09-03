"""Configuration for Recoup ML components."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MLSettings(BaseSettings):
    """Runtime settings shared by training and inference code."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    ml_artifacts_dir: Path = Field(
        default=Path("./src/ml/artifacts_store"),
        validation_alias="ML_ARTIFACTS_DIR",
    )
    ml_random_seed: int = Field(default=42, validation_alias="ML_RANDOM_SEED")
    ml_confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        validation_alias="ML_CONFIDENCE_THRESHOLD",
    )
    recovery_horizon_days: int = Field(
        default=30,
        gt=0,
        validation_alias="RECOVERY_HORIZON_DAYS",
    )

    #: Which recovery scorer the agent uses. False keeps the hand-written
    #: rules-based scorer in charge; True switches to the trained model, which
    #: still falls back to the rules per-invoice on any failure. Defaults to
    #: False so a fresh clone with no trained artifact behaves predictably.
    use_model_scorer: bool = Field(default=False, validation_alias="USE_MODEL_SCORER")

    def ensure_artifacts_dir(self) -> Path:
        """Create and return the configured artifact directory."""

        self.ml_artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self.ml_artifacts_dir
