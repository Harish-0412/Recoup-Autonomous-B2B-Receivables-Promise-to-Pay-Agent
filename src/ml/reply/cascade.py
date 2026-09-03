"""The cascade: trained classifier first, LLM only when it is unsure.

Stage C (TF-IDF + calibrated linear SVM) handles the common cases in
milliseconds, with an explanation naming the exact n-grams that drove it.
Stage A (the LLM) is consulted only when Stage C's calibrated probability falls
below the threshold -- the genuinely ambiguous replies, which is where an LLM's
generality is worth its latency and cost.

This is the piece that makes the confidence calibration load-bearing rather
than decorative: the threshold only routes sensibly if a stated 0.6 means
roughly a 60% chance of being right, which ``evaluation.calibration_report``
checks against held-out data.
"""

from datetime import datetime
from typing import Any

from src.ml.config import MLSettings
from src.ml.reply.classifier import (
    MODEL_NAME,
    ReplyIntentClassifier,
    load_classifier,
)
from src.ml.reply.llm_baseline import StructuredLLM, classify_reply_llm
from src.ml.schemas import (
    ExtractedEntities,
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    IntentLabel,
    ReplyIntentPrediction,
)
from src.ml.versioning import utc_now


def threshold_for_intent(
    intent: IntentLabel,
    base_threshold: float,
    settings: MLSettings | None = None,
) -> float:
    """The confidence Stage C must reach before this intent is acted on.

    One threshold for every class assumes every mistake costs the same, and
    they do not. DISPUTE is the expensive false positive in this system: acting
    on it freezes escalation and hands the invoice to a human, so a wrong one
    silently stops collection on a customer who never disputed anything. It is
    held to a higher bar, and borderline cases go to the LLM instead.

    OPT_OUT is deliberately left on the base threshold. There the costly error
    runs the other way -- missing a genuine opt-out is a compliance failure,
    while a false positive costs one silenced reminder -- and it already has a
    deterministic guard in ``service.py`` that does not consult confidence
    at all.
    """

    active_settings = settings or MLSettings()
    if intent is IntentLabel.DISPUTE:
        return max(base_threshold, active_settings.ml_dispute_confidence_threshold)
    return base_threshold


async def classify_reply_cascade(
    raw_text: str,
    invoice_context: dict[str, Any] | None = None,
    *,
    invoice_id: str = "",
    reply_id: str = "",
    client: StructuredLLM | None = None,
    reference_dt: datetime | None = None,
    settings: MLSettings | None = None,
    model: ReplyIntentClassifier | None = None,
    model_version: str = "latest",
    threshold: float | None = None,
) -> ReplyIntentPrediction:
    """Classify a reply with Stage C, escalating to Stage A when unsure.

    Never raises. If no trained artifact exists yet, the whole call degrades to
    the LLM baseline -- so binding this as the primary classifier is safe before
    the first training run has happened.
    """

    active_settings = settings or MLSettings()
    active_threshold = (
        threshold if threshold is not None else active_settings.ml_confidence_threshold
    )
    resolved_invoice_id = invoice_id or str((invoice_context or {}).get("invoice_id") or "unknown")
    resolved_reply_id = reply_id or "unknown"

    async def escalate(reason: FallbackReason, detail: str) -> ReplyIntentPrediction:
        """Hand the reply to Stage A and record why."""

        prediction = await classify_reply_llm(
            raw_text,
            invoice_context,
            invoice_id=resolved_invoice_id,
            reply_id=resolved_reply_id,
            client=client,
            reference_dt=reference_dt,
        )
        if prediction.fallback_used:
            # Stage A failed too -- keep its own fallback record, which is more
            # specific than "we escalated".
            return prediction

        explanation = prediction.explanation or ""
        return prediction.model_copy(
            update={
                "fallback_used": True,
                "fallback": FallbackResult(
                    triggered=True,
                    reason=reason,
                    resolved_by=FallbackResolver.LLM_FALLBACK,
                ),
                "explanation": f"{detail} {explanation}".strip(),
            }
        )

    if not raw_text or not raw_text.strip():
        return await escalate(FallbackReason.INVALID_OUTPUT, "Empty reply text.")

    active_model = model
    if active_model is None:
        try:
            active_model, _ = load_classifier(model_version, settings=active_settings)
        except Exception as exc:
            return await escalate(
                FallbackReason.MODEL_LOAD_FAILED,
                f"Stage C unavailable ({exc}); answered by the LLM baseline.",
            )

    try:
        result = active_model.predict_one(raw_text)
    except Exception as exc:
        return await escalate(
            FallbackReason.UNEXPECTED_ERROR,
            f"Stage C raised ({exc}); answered by the LLM baseline.",
        )

    required = threshold_for_intent(result.intent, active_threshold, active_settings)
    if result.confidence < required:
        raised = " (raised for this intent)" if required > active_threshold else ""
        return await escalate(
            FallbackReason.LOW_CONFIDENCE,
            (
                f"Stage C was {result.confidence:.2f} confident in "
                f"{result.intent.value}, below the {required:.2f} threshold{raised}; "
                "escalated to the LLM."
            ),
        )

    return ReplyIntentPrediction(
        model_version=MODEL_NAME,
        confidence=result.confidence,
        fallback_used=False,
        scored_at=utc_now(),
        reply_id=resolved_reply_id,
        invoice_id=resolved_invoice_id,
        raw_text=raw_text,
        intent=result.intent,
        entities=ExtractedEntities(),
        model_used=MODEL_NAME,
        explanation=result.explanation(),
        fallback=None,
    )


def install_cascade_classifier() -> None:
    """Make the cascade the primary classifier for ``understand_reply``.

    Not called on import: rebinding a module-level default as a side effect of
    an import is the kind of thing that makes test failures hard to explain.
    Call it explicitly at application startup.
    """

    from src.ml.reply.service import set_primary_classifier

    set_primary_classifier(classify_reply_cascade)
