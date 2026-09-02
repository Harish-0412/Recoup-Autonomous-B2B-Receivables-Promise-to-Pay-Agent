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

    def ensure_artifacts_dir(self) -> Path:
        """Create and return the configured artifact directory."""

        self.ml_artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self.ml_artifacts_dir
