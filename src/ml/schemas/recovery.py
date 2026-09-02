"""Contracts for the invoice recovery-probability model."""

from pydantic import BaseModel, ConfigDict, Field

from src.ml.schemas.base import FallbackResult, PredictionEnvelope


class FeatureDriver(BaseModel):
    """One feature's contribution to a single recovery score."""

    model_config = ConfigDict(protected_namespaces=())

    feature: str = Field(min_length=1)
    value: float
    shap_contribution: float


class RecoveryScorePrediction(PredictionEnvelope):
    """Probability that an invoice is recovered inside the configured horizon."""

    invoice_id: str = Field(min_length=1)
    p_recovery_30d: float = Field(ge=0.0, le=1.0)
    calibrated: bool
    top_drivers: list[FeatureDriver] = Field(default_factory=list)
    fallback: FallbackResult | None = None
