"""The reply-understanding entry point: raw text in, structured intent out.

Three things happen here that do not belong in either the classifier or the
extractor:

1. **The classifier is a binding, not a call.** ``primary_classifier`` points at
   the LLM baseline today. When Phase 4's trained classifier ships, only this
   binding changes -- the LLM becomes the low-confidence fallback rather than
   being ripped out.
2. **Deterministic extraction fills the gaps the model left.** The regex/
   dateparser extractors are cheap, auditable, and often more precise on
   amounts than the model is, so they backfill any entity the model returned
   as null. They never overwrite a value the model supplied.
3. **Opt-out is not a model decision.** Any plausible request to stop contact
   is binding regardless of what the classifier said or how confident it was.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from src.ml.config import MLSettings
from src.ml.reply.entity_extraction import (
    extract_amount,
    extract_date,
    extract_dispute_reason,
    normalize_currency,
)
from src.ml.reply.llm_baseline import StructuredLLM, classify_reply_llm
from src.ml.schemas import (
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    IntentLabel,
    ReplyIntentPrediction,
)
from src.ml.versioning import utc_now

#: Intents for which a promised amount or date is a meaningful field. A reply
#: such as "office closed until next week" contains a date, but it is not a
#: payment commitment and must not be recorded as one.
ENTITY_BEARING_INTENTS: frozenset[IntentLabel] = frozenset(
    {
        IntentLabel.PROMISE_TO_PAY,
        IntentLabel.NEGOTIATION_REQUEST,
        IntentLabel.PARTIAL_PAYMENT_CLAIM,
        IntentLabel.ALREADY_PAID_CLAIM,
    }
)

#: Explicit requests to stop contact. Matched before and after classification.
_OPT_OUT_PATTERNS: tuple[str, ...] = (
    r"\bunsubscribe\b",
    r"\bopt\s*(?:me|us)?\s*out\b",
    r"\bstop\s+(?:messag\w+|send\w+|contact\w+|call\w+|text\w+|reminder\w*)\b",
    r"\bstop\s+(?:this|these)\b",
    r"\b(?:do\s*n[o']?t|dont|don't|never)\s+(?:contact|message|msg|call|text|whatsapp)\b",
    r"\bremove\s+(?:my|this|our)\s+(?:number|contact|email|details)\b",
    r"\btake\s+me\s+off\b",
    r"\bmat\s+bhej\w*\b",
    r"\bband\s+kar\w*\b",
)

_OPT_OUT_REGEX = re.compile("|".join(_OPT_OUT_PATTERNS), re.IGNORECASE)

PrimaryClassifier = Callable[..., Awaitable[ReplyIntentPrediction]]

#: Phase 3: the LLM baseline. Phase 4: rebind to the trained classifier with
#: ``set_primary_classifier`` and this module keeps working unchanged.
primary_classifier: PrimaryClassifier = classify_reply_llm


def set_primary_classifier(classifier: PrimaryClassifier) -> PrimaryClassifier:
    """Swap the primary classifier and return the previous one."""

    global primary_classifier
    previous = primary_classifier
    primary_classifier = classifier
    return previous


def looks_like_opt_out(text: str) -> bool:
    """Deterministic opt-out detection.

    Never gated on model confidence: an unhonoured opt-out is a compliance
    failure, and a false positive only costs one silenced reminder.
    """

    return bool(text) and bool(_OPT_OUT_REGEX.search(text))


def _apply_opt_out_guard(prediction: ReplyIntentPrediction) -> ReplyIntentPrediction:
    if prediction.intent is IntentLabel.OPT_OUT or not looks_like_opt_out(prediction.raw_text):
        return prediction

    note = "Deterministic opt-out guard: an explicit stop-contact request overrides the classifier."
    return prediction.model_copy(
        update={
            "intent": IntentLabel.OPT_OUT,
            "confidence": 1.0,
            "explanation": f"{note} (classifier said {prediction.intent.value})",
        }
    )


def _backfill_entities(
    prediction: ReplyIntentPrediction,
    reference_dt: datetime,
) -> ReplyIntentPrediction:
    """Fill nulls from the deterministic extractors, then gate on intent."""

    entities = prediction.entities.model_copy(deep=True)

    if prediction.intent in ENTITY_BEARING_INTENTS:
        if entities.promised_amount is None:
            entities.promised_amount = extract_amount(prediction.raw_text)
        if entities.promised_date is None:
            entities.promised_date = extract_date(prediction.raw_text, reference_dt)
    else:
        # A date or amount mentioned in a query, an opt-out or small talk is
        # not a promise. Do not record one.
        entities.promised_amount = None
        entities.promised_date = None

    if prediction.intent is IntentLabel.DISPUTE:
        if entities.dispute_reason is None:
            entities.dispute_reason = extract_dispute_reason(prediction.raw_text)
    else:
        entities.dispute_reason = None

    entities.currency = entities.currency or normalize_currency(prediction.raw_text)

    return prediction.model_copy(update={"entities": entities})


def _apply_confidence_policy(
    prediction: ReplyIntentPrediction,
    threshold: float,
) -> ReplyIntentPrediction:
    """Route an uncertain read to human review rather than acting on it."""

    if prediction.fallback_used or prediction.confidence >= threshold:
        return prediction
    if prediction.intent is IntentLabel.OPT_OUT:
        # Opt-out is binding at any confidence.
        return prediction

    explanation = prediction.explanation or ""
    suffix = (
        f"Confidence {prediction.confidence:.2f} is below the {threshold:.2f} "
        "threshold; routed for human review."
    )
    return prediction.model_copy(
        update={
            "fallback_used": True,
            "fallback": FallbackResult(
                triggered=True,
                reason=FallbackReason.LOW_CONFIDENCE,
                resolved_by=FallbackResolver.HUMAN_REVIEW_QUEUE,
            ),
            "explanation": f"{explanation} {suffix}".strip(),
        }
    )


async def understand_reply(
    raw_text: str,
    invoice_id: str,
    reply_id: str,
    *,
    invoice_context: dict[str, Any] | None = None,
    client: StructuredLLM | None = None,
    reference_dt: datetime | None = None,
    settings: MLSettings | None = None,
) -> ReplyIntentPrediction:
    """Turn a raw customer reply into a schema-conformant prediction.

    Always returns a valid ``ReplyIntentPrediction``. Failures degrade into a
    fallback-flagged prediction; they never propagate as exceptions.
    """

    active_settings = settings or MLSettings()
    reference = reference_dt or utc_now()
    context = dict(invoice_context or {})
    context.setdefault("invoice_id", invoice_id)

    try:
        prediction = await primary_classifier(
            raw_text,
            context,
            invoice_id=invoice_id,
            reply_id=reply_id,
            client=client,
            reference_dt=reference,
        )
    except Exception as exc:  # a classifier that raises is still a classifier failure
        prediction = ReplyIntentPrediction(
            model_version="unavailable",
            confidence=0.0,
            fallback_used=True,
            scored_at=utc_now(),
            reply_id=reply_id,
            invoice_id=invoice_id,
            raw_text=raw_text,
            intent=IntentLabel.OTHER,
            model_used="none",
            explanation=f"Classifier raised: {exc}",
            fallback=FallbackResult(
                triggered=True,
                reason=FallbackReason.UNEXPECTED_ERROR,
                resolved_by=FallbackResolver.HUMAN_REVIEW_QUEUE,
            ),
        )

    prediction = _apply_opt_out_guard(prediction)
    prediction = _backfill_entities(prediction, reference)
    prediction = _apply_confidence_policy(prediction, active_settings.ml_confidence_threshold)
    return prediction


def understand_reply_sync(
    raw_text: str,
    invoice_id: str,
    reply_id: str,
    **kwargs: Any,
) -> ReplyIntentPrediction:
    """Blocking wrapper for scripts, notebooks and offline evaluation.

    Not for use inside the FastAPI request path -- await ``understand_reply``
    there instead.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(understand_reply(raw_text, invoice_id, reply_id, **kwargs))

    raise RuntimeError(
        "understand_reply_sync cannot be called from a running event loop; "
        "await understand_reply instead"
    )
