"""Contracts for customer reply understanding (intent + entities)."""

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.ml.schemas.base import FallbackResult, PredictionEnvelope


class IntentLabel(str, Enum):
    """The full reply-intent taxonomy.

    All eight values are part of the contract from day one. The Phase 3 LLM
    baseline will be stronger on some than others; the schema does not change
    when the trained classifier in Phase 4 closes that gap.
    """

    PROMISE_TO_PAY = "PROMISE_TO_PAY"
    DISPUTE = "DISPUTE"
    OPT_OUT = "OPT_OUT"
    NEGOTIATION_REQUEST = "NEGOTIATION_REQUEST"
    PARTIAL_PAYMENT_CLAIM = "PARTIAL_PAYMENT_CLAIM"
    ALREADY_PAID_CLAIM = "ALREADY_PAID_CLAIM"
    GENERAL_QUERY = "GENERAL_QUERY"
    OTHER = "OTHER"


class ExtractedEntities(BaseModel):
    """Structured values pulled out of a free-text reply."""

    model_config = ConfigDict(protected_namespaces=())

    promised_amount: float | None = Field(default=None, ge=0.0)
    promised_date: date | None = None
    currency: str = Field(default="INR", min_length=3, max_length=3)
    dispute_reason: str | None = None

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalpha():
            raise ValueError("currency must be a 3-letter alphabetic code")
        return normalized


class ReplyIntentPrediction(PredictionEnvelope):
    """One classified customer reply, ready for the policy engine to consume."""

    reply_id: str = Field(min_length=1)
    invoice_id: str = Field(min_length=1)
    raw_text: str
    intent: IntentLabel
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    model_used: str = Field(min_length=1)
    explanation: str | None = None
    fallback: FallbackResult | None = None
