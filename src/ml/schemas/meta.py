"""Metadata stored next to every trained ML artifact."""

from datetime import datetime

from pydantic import BaseModel, Field


class ModelMetadata(BaseModel):
    """Versioned metadata for a saved model artifact."""

    model_name: str
    model_version: str
    trained_at: datetime
    train_rows: int = Field(ge=0)
    feature_columns: list[str]
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: str | None = None
