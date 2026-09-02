"""Base contracts shared by every ML prediction in Recoup.

``PredictionEnvelope`` exists so the four fields the reasoning lane always needs --
model version, confidence, fallback flag, and a scoring timestamp -- are structural.
A new prediction type inherits them; it cannot forget them.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.ml.versioning import utc_now


class FallbackReason(str, Enum):
    """Why a prediction could not be served by the primary model."""

    MODEL_LOAD_FAILED = "model_load_failed"
    LOW_CONFIDENCE = "low_confidence"
    MALFORMED_OUTPUT = "malformed_output"
    INVALID_OUTPUT = "invalid_output"
    TIMEOUT = "timeout"
    UNEXPECTED_ERROR = "unexpected_error"


class FallbackResolver(str, Enum):
    """Who produced the answer once the primary model stepped aside."""

    RULES_BASED_SCORER = "rules_based_scorer"
    LLM_FALLBACK = "llm_fallback"
    HUMAN_REVIEW_QUEUE = "human_review_queue"


class FallbackResult(BaseModel):
    """Audit record of a degraded prediction path."""

    model_config = ConfigDict(protected_namespaces=())

    triggered: bool
    reason: FallbackReason | None = None
    resolved_by: FallbackResolver | None = None

    @model_validator(mode="after")
    def _require_reason_when_triggered(self) -> "FallbackResult":
        if self.triggered and self.reason is None:
            raise ValueError("reason is required when a fallback is triggered")
        return self


class PredictionEnvelope(BaseModel):
    """Fields every Recoup ML prediction must carry.

    Subclasses that declare a ``fallback: FallbackResult | None`` field get the
    ``fallback_used`` / ``fallback.triggered`` consistency check for free.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_version: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    fallback_used: bool = False
    scored_at: datetime = Field(default_factory=utc_now)

    @field_validator("scored_at")
    @classmethod
    def _ensure_timezone_aware(cls, value: datetime) -> datetime:
        """Treat a naive timestamp as UTC so downstream comparisons never mix kinds."""

        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @model_validator(mode="after")
    def _fallback_flag_matches_detail(self) -> "PredictionEnvelope":
        fallback = getattr(self, "fallback", None)
        if fallback is not None and fallback.triggered != self.fallback_used:
            raise ValueError("fallback_used must match fallback.triggered")
        return self
