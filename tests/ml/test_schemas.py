from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.ml.schemas import (
    ExtractedEntities,
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    FeatureDriver,
    IntentLabel,
    ModelMetadata,
    PredictionEnvelope,
    RecoveryScorePrediction,
    ReplyIntentPrediction,
)
from src.ml.versioning import utc_now


def _reply_prediction(**overrides) -> ReplyIntentPrediction:
    payload = {
        "model_version": "llm-groq-baseline-20260902-a1b2c3",
        "confidence": 0.88,
        "scored_at": utc_now(),
        "reply_id": "rpl_8f21ac",
        "invoice_id": "INV-1043",
        "raw_text": "will clear 1.8L by fri, waive the late fee pls",
        "intent": IntentLabel.NEGOTIATION_REQUEST,
        "entities": ExtractedEntities(
            promised_amount=180000.0,
            promised_date=date(2026, 9, 11),
        ),
        "model_used": "llm-groq-baseline",
        "explanation": "Customer commits to an amount but asks for a fee waiver.",
    }
    payload.update(overrides)
    return ReplyIntentPrediction(**payload)


def _recovery_prediction(**overrides) -> RecoveryScorePrediction:
    payload = {
        "model_version": "recovery-xgb-20260902-a1b2c3",
        "confidence": 0.71,
        "scored_at": utc_now(),
        "invoice_id": "INV-1043",
        "p_recovery_30d": 0.71,
        "calibrated": True,
        "top_drivers": [
            FeatureDriver(
                feature="customer_on_time_ratio_90d",
                value=0.42,
                shap_contribution=-0.18,
            )
        ],
    }
    payload.update(overrides)
    return RecoveryScorePrediction(**payload)


# --- envelope is structural, not a convention -------------------------------


@pytest.mark.parametrize("schema", [ReplyIntentPrediction, RecoveryScorePrediction])
def test_predictions_inherit_the_envelope(schema):
    assert PredictionEnvelope in schema.__mro__

    required = {"model_version", "confidence", "fallback_used", "scored_at"}
    assert required <= set(schema.model_fields)


@pytest.mark.parametrize("bad_confidence", [1.4, -0.1])
def test_confidence_outside_unit_interval_is_rejected(bad_confidence):
    with pytest.raises(ValidationError):
        _reply_prediction(confidence=bad_confidence)

    with pytest.raises(ValidationError):
        _recovery_prediction(confidence=bad_confidence)


def test_fallback_used_defaults_to_false():
    assert _reply_prediction().fallback_used is False
    assert _recovery_prediction().fallback_used is False


def test_scored_at_defaults_to_now_and_is_timezone_aware():
    fresh = ReplyIntentPrediction(
        model_version="llm-groq-baseline-20260902-a1b2c3",
        confidence=0.5,
        reply_id="rpl_1",
        invoice_id="INV-1",
        raw_text="ok",
        intent=IntentLabel.GENERAL_QUERY,
        model_used="llm-groq-baseline",
    )

    assert fresh.scored_at.tzinfo is not None
    assert _reply_prediction().scored_at.tzinfo is not None


def test_naive_scored_at_is_treated_as_utc():
    prediction = _reply_prediction(scored_at=datetime(2026, 9, 5, 9, 42, 11))

    assert prediction.scored_at.tzinfo == UTC


# --- fallback contract ------------------------------------------------------


def test_fallback_requires_a_reason_when_triggered():
    with pytest.raises(ValidationError):
        FallbackResult(triggered=True)


def test_fallback_flag_must_match_fallback_detail():
    with pytest.raises(ValidationError):
        _reply_prediction(
            fallback_used=False,
            fallback=FallbackResult(
                triggered=True,
                reason=FallbackReason.MALFORMED_OUTPUT,
                resolved_by=FallbackResolver.HUMAN_REVIEW_QUEUE,
            ),
        )


def test_degraded_prediction_is_valid_when_flag_and_detail_agree():
    prediction = _reply_prediction(
        intent=IntentLabel.OTHER,
        confidence=0.0,
        fallback_used=True,
        fallback=FallbackResult(
            triggered=True,
            reason=FallbackReason.TIMEOUT,
            resolved_by=FallbackResolver.HUMAN_REVIEW_QUEUE,
        ),
    )

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.TIMEOUT


# --- reply schema -----------------------------------------------------------


def test_intent_taxonomy_has_all_eight_classes():
    assert [label.value for label in IntentLabel] == [
        "PROMISE_TO_PAY",
        "DISPUTE",
        "OPT_OUT",
        "NEGOTIATION_REQUEST",
        "PARTIAL_PAYMENT_CLAIM",
        "ALREADY_PAID_CLAIM",
        "GENERAL_QUERY",
        "OTHER",
    ]


def test_entities_default_to_empty_inr():
    entities = ExtractedEntities()

    assert entities.promised_amount is None
    assert entities.promised_date is None
    assert entities.currency == "INR"
    assert entities.dispute_reason is None


def test_currency_is_normalized_to_uppercase():
    assert ExtractedEntities(currency="inr").currency == "INR"


def test_negative_promised_amount_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedEntities(promised_amount=-1.0)


# --- recovery schema --------------------------------------------------------


@pytest.mark.parametrize("bad_probability", [1.01, -0.01])
def test_recovery_probability_outside_unit_interval_is_rejected(bad_probability):
    with pytest.raises(ValidationError):
        _recovery_prediction(p_recovery_30d=bad_probability)


def test_top_drivers_default_to_an_independent_empty_list():
    first = _recovery_prediction(top_drivers=[])
    second = _recovery_prediction(top_drivers=[])

    first.top_drivers.append(
        FeatureDriver(feature="days_overdue_at_scoring", value=12.0, shap_contribution=-0.07)
    )

    assert second.top_drivers == []


# --- metadata schema --------------------------------------------------------


def test_model_metadata_matches_what_artifacts_writes():
    metadata = ModelMetadata(
        model_name="recovery-xgb",
        model_version="recovery-xgb-20260902-a1b2c3",
        trained_at=utc_now(),
        train_rows=600,
        feature_columns=["customer_on_time_ratio_90d", "invoice_amount"],
        metrics={"auc": 0.81, "brier": 0.14},
    )

    assert metadata.notes is None
    assert metadata.metrics["auc"] == pytest.approx(0.81)


def test_model_metadata_rejects_negative_train_rows():
    with pytest.raises(ValidationError):
        ModelMetadata(
            model_name="recovery-xgb",
            model_version="recovery-xgb-20260902-a1b2c3",
            trained_at=utc_now(),
            train_rows=-1,
            feature_columns=[],
        )


# --- serialization round-trips ----------------------------------------------


def test_every_schema_round_trips_through_json():
    samples = [
        _reply_prediction(),
        _recovery_prediction(),
        ExtractedEntities(promised_amount=45000.0, promised_date=date(2026, 9, 11)),
        FeatureDriver(feature="invoice_amount", value=180000.0, shap_contribution=0.03),
        FallbackResult(
            triggered=True,
            reason=FallbackReason.LOW_CONFIDENCE,
            resolved_by=FallbackResolver.LLM_FALLBACK,
        ),
        ModelMetadata(
            model_name="tfidf-svm-intent",
            model_version="tfidf-svm-intent-20260902-a1b2c3",
            trained_at=utc_now(),
            train_rows=1200,
            feature_columns=["tfidf"],
            metrics={"macro_f1": 0.79},
            notes="phase 1 contract check",
        ),
    ]

    for sample in samples:
        restored = type(sample).model_validate_json(sample.model_dump_json())
        assert restored == sample, f"round-trip changed {type(sample).__name__}"
