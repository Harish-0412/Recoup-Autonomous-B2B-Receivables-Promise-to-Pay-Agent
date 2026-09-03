"""Metadata stored next to every trained ML artifact."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelMetadata(BaseModel):
    """Versioned metadata for a saved model artifact.

    This is the single definition of "what a trained model's metadata looks like":
    ``src.ml.artifacts`` writes it as ``meta.json``, training scripts produce it,
    and model cards read it.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_name: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    trained_at: datetime
    train_rows: int = Field(ge=0)
    feature_columns: list[str]
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("trained_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
