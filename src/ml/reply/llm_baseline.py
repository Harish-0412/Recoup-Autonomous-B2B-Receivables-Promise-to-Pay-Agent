"""LLM zero-shot baseline for reply intent classification.

This is the Stage A classifier: it needs no training data and works on day
one. Phase 4 swaps a trained classifier into the primary slot and demotes this
to the low-confidence fallback -- see ``src.ml.reply.service``.

``classify_reply_llm`` never raises. Every failure mode -- no provider
configured, a timeout, prose instead of JSON, an unknown intent label -- comes
back as a valid ``ReplyIntentPrediction`` carrying ``fallback_used=True`` and a
reason. A collections agent that crashes on a strangely worded reply is worse
than one that routes it to a human.
"""

import asyncio
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.ml.reply.entity_extraction import extract_amount, extract_date, normalize_currency
from src.ml.reply.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_classification_prompt,
)
from src.ml.schemas import (
    ExtractedEntities,
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    IntentLabel,
    ReplyIntentPrediction,
)
from src.ml.versioning import utc_now

MODEL_USED = "llm-baseline"
DEFAULT_TIMEOUT_SECONDS = 20.0


@runtime_checkable
class StructuredLLM(Protocol):
    """The one method this module needs from the shared LLM client.

    Depending on the protocol rather than the concrete class keeps the ML
    package importable without the provider SDKs, and makes the classifier
    trivially testable with a stub.
    """

    async def generate_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: str | None = ...,
        retry_prompt: str | None = ...,
    ) -> BaseModel: ...


class ReplyIntentLLMOutput(BaseModel):
    """What the LLM is asked to return.

    Deliberately lenient about *formatting* and strict about *meaning*: an
    amount written as "Rs. 45,000" is recovered, but an intent outside the
    taxonomy is a validation failure that triggers the retry.
    """

    model_config = ConfigDict(protected_namespaces=())

    intent: IntentLabel
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str | None = None
    promised_amount: float | None = None
    promised_date: str | None = None
    currency: str | None = None
    dispute_reason: str | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> Any:
        """Accept a percentage or an out-of-range number rather than failing.

        A model that says 95 when it means 0.95 has still classified the reply
        correctly; rejecting the whole response over it would be wasteful.
        """

        if isinstance(value, str):
            value = value.strip().rstrip("%")
            try:
                value = float(value)
            except ValueError:
                return value
        if isinstance(value, int | float):
            number = float(value)
            if number >= 2.0:
                # 95 means 95%. A value just above 1 is a malformed
                # probability, not a percentage, so it is clamped instead.
                number = number / 100.0
            return min(max(number, 0.0), 1.0)
        return value

    @field_validator("promised_amount", mode="before")
    @classmethod
    def _coerce_amount(cls, value: Any) -> Any:
        """Recover a number from '45,000', 'Rs. 45000' or '1.8L'."""

        if isinstance(value, str):
            return extract_amount(value)
        return value

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip():
            return normalize_currency(value)
        return None


def get_structured_llm() -> StructuredLLM:
    """Resolve the project's shared LLM client.

    Imported lazily so that importing ``src.ml`` does not pull in the provider
    SDKs or require API keys -- offline tests and training scripts never touch
    this path.
    """

    from app.services.llm_client import get_llm_client

    return get_llm_client()


def _fallback_prediction(
    *,
    raw_text: str,
    invoice_id: str,
    reply_id: str,
    reason: FallbackReason,
    resolved_by: FallbackResolver = FallbackResolver.HUMAN_REVIEW_QUEUE,
    detail: str | None = None,
) -> ReplyIntentPrediction:
    """A schema-valid prediction for a reply the model could not read."""

    return ReplyIntentPrediction(
        model_version=PROMPT_VERSION,
        confidence=0.0,
        fallback_used=True,
        scored_at=utc_now(),
        reply_id=reply_id,
        invoice_id=invoice_id,
        raw_text=raw_text,
        intent=IntentLabel.OTHER,
        entities=ExtractedEntities(),
        model_used=MODEL_USED,
        explanation=detail or f"Classification unavailable ({reason.value}).",
        fallback=FallbackResult(triggered=True, reason=reason, resolved_by=resolved_by),
    )


async def classify_reply_llm(
    raw_text: str,
    invoice_context: dict[str, Any] | None = None,
    *,
    invoice_id: str = "",
    reply_id: str = "",
    client: StructuredLLM | None = None,
    reference_dt: datetime | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ReplyIntentPrediction:
    """Classify one reply with the LLM baseline. Never raises."""

    resolved_invoice_id = invoice_id or str((invoice_context or {}).get("invoice_id") or "unknown")
    resolved_reply_id = reply_id or "unknown"
    reference = reference_dt or utc_now()

    def fallback(reason: FallbackReason, detail: str | None = None) -> ReplyIntentPrediction:
        return _fallback_prediction(
            raw_text=raw_text,
            invoice_id=resolved_invoice_id,
            reply_id=resolved_reply_id,
            reason=reason,
            detail=detail,
        )

    if not raw_text or not raw_text.strip():
        return fallback(FallbackReason.INVALID_OUTPUT, "Empty reply text.")

    try:
        active_client = client or get_structured_llm()
    except Exception as exc:  # provider missing, keys unset, import failure
        return fallback(FallbackReason.MODEL_LOAD_FAILED, f"LLM client unavailable: {exc}")

    prompt = build_classification_prompt(raw_text, invoice_context)
    retry_prompt = build_classification_prompt(raw_text, invoice_context, strict=True)

    try:
        output = await asyncio.wait_for(
            active_client.generate_structured(
                prompt,
                ReplyIntentLLMOutput,
                system_prompt=SYSTEM_PROMPT,
                retry_prompt=retry_prompt,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return fallback(FallbackReason.TIMEOUT, "LLM did not respond in time.")
    except Exception as exc:
        return fallback(FallbackReason.MALFORMED_OUTPUT, f"LLM output unusable: {exc}")

    if not isinstance(output, ReplyIntentLLMOutput):
        return fallback(FallbackReason.MALFORMED_OUTPUT, "Unexpected response type from client.")

    promised_date = extract_date(output.promised_date, reference) if output.promised_date else None

    return ReplyIntentPrediction(
        model_version=PROMPT_VERSION,
        confidence=output.confidence,
        fallback_used=False,
        scored_at=utc_now(),
        reply_id=resolved_reply_id,
        invoice_id=resolved_invoice_id,
        raw_text=raw_text,
        intent=output.intent,
        entities=ExtractedEntities(
            promised_amount=output.promised_amount,
            promised_date=promised_date,
            currency=output.currency or normalize_currency(raw_text),
            dispute_reason=output.dispute_reason,
        ),
        model_used=MODEL_USED,
        explanation=output.explanation,
        fallback=None,
    )
