import asyncio
from datetime import date, datetime

import pytest

from src.ml.config import MLSettings
from src.ml.reply.cascade import classify_reply_cascade, install_cascade_classifier
from src.ml.reply.classifier import (
    MODEL_NAME,
    ReplyIntentClassifier,
    clear_classifier_cache,
)
from src.ml.reply.dataset import SplitName, build_corpus, corpus_texts_and_labels
from src.ml.reply.llm_baseline import MODEL_USED as LLM_MODEL
from src.ml.reply.llm_baseline import ReplyIntentLLMOutput
from src.ml.reply.service import understand_reply
from src.ml.schemas import FallbackReason, FallbackResolver, IntentLabel, ReplyIntentPrediction

REFERENCE = datetime(2026, 9, 1)


def run(coro):
    return asyncio.run(coro)


class StubLLM:
    """Stage A stand-in that records whether it was consulted."""

    def __init__(self, intent=IntentLabel.GENERAL_QUERY, confidence=0.9, error=None):
        self.intent = intent
        self.confidence = confidence
        self.error = error
        self.calls = 0

    async def generate_structured(self, prompt, schema, system_prompt=None, retry_prompt=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ReplyIntentLLMOutput(
            intent=self.intent,
            confidence=self.confidence,
            explanation="Stage A stand-in.",
        )


@pytest.fixture(scope="module")
def trained():
    corpus = build_corpus(seed=42)
    texts, labels = corpus_texts_and_labels(corpus.for_split(SplitName.TRAIN))
    return ReplyIntentClassifier.fit(texts, labels, seed=42)


@pytest.fixture(autouse=True)
def isolate_state():
    from src.ml.reply import service

    original = service.primary_classifier
    clear_classifier_cache()
    yield
    service.primary_classifier = original
    clear_classifier_cache()


@pytest.fixture
def empty_settings(tmp_path):
    """Settings pointing at an artifact store with no trained model in it."""

    return MLSettings(ml_artifacts_dir=tmp_path / "empty")


# --- Stage C resolves confident cases --------------------------------------


def test_confident_replies_are_answered_by_the_trained_model(trained):
    stub = StubLLM()

    prediction = run(
        classify_reply_cascade(
            "Stop messaging me. Remove this number from your list.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            model=trained,
            reference_dt=REFERENCE,
            threshold=0.6,
        )
    )

    assert prediction.intent is IntentLabel.OPT_OUT
    assert prediction.model_used == MODEL_NAME
    assert prediction.fallback_used is False
    assert stub.calls == 0, "Stage A must not be consulted for a confident case"


def test_a_stage_c_answer_carries_its_evidence(trained):
    prediction = run(
        classify_reply_cascade(
            "The quantities billed do not match our goods receipt note.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=StubLLM(),
            model=trained,
            threshold=0.5,
        )
    )

    assert prediction.explanation
    assert prediction.intent.value in prediction.explanation


# --- Stage A picks up the uncertain ones ------------------------------------


def test_low_confidence_escalates_to_the_llm(trained):
    stub = StubLLM(intent=IntentLabel.NEGOTIATION_REQUEST, confidence=0.93)

    prediction = run(
        classify_reply_cascade(
            "hmm",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            model=trained,
            reference_dt=REFERENCE,
            threshold=0.99,  # force escalation
        )
    )

    assert stub.calls == 1
    assert prediction.model_used == LLM_MODEL
    assert prediction.intent is IntentLabel.NEGOTIATION_REQUEST
    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.LOW_CONFIDENCE
    assert prediction.fallback.resolved_by is FallbackResolver.LLM_FALLBACK


def test_the_escalation_explains_why_it_escalated(trained):
    prediction = run(
        classify_reply_cascade(
            "will see",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=StubLLM(),
            model=trained,
            threshold=0.99,
        )
    )

    assert "below the" in (prediction.explanation or "")
    assert "escalated to the LLM" in (prediction.explanation or "")


def test_a_threshold_of_zero_never_escalates(trained):
    stub = StubLLM()

    run(
        classify_reply_cascade(
            "Ok.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            model=trained,
            threshold=0.0,
        )
    )

    assert stub.calls == 0


# --- degradation ------------------------------------------------------------


def test_a_missing_artifact_degrades_to_the_llm(empty_settings):
    stub = StubLLM(intent=IntentLabel.PROMISE_TO_PAY, confidence=0.9)

    prediction = run(
        classify_reply_cascade(
            "Will clear Rs. 45,000 by next Friday.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            settings=empty_settings,
        )
    )

    assert stub.calls == 1
    assert prediction.intent is IntentLabel.PROMISE_TO_PAY
    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.MODEL_LOAD_FAILED


def test_a_stage_c_crash_degrades_to_the_llm():
    class ExplodingModel:
        def predict_one(self, text):
            raise RuntimeError("model exploded")

    stub = StubLLM(intent=IntentLabel.DISPUTE, confidence=0.88)

    prediction = run(
        classify_reply_cascade(
            "This bill is wrong.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            model=ExplodingModel(),
        )
    )

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.UNEXPECTED_ERROR
    assert prediction.intent is IntentLabel.DISPUTE


def test_both_stages_failing_still_returns_a_valid_prediction(empty_settings):
    stub = StubLLM(error=RuntimeError("provider down"))

    prediction = run(
        classify_reply_cascade(
            "Will pay Friday.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            settings=empty_settings,
        )
    )

    assert isinstance(prediction, ReplyIntentPrediction)
    assert prediction.intent is IntentLabel.OTHER
    assert prediction.fallback_used is True
    assert prediction.fallback is not None


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_text_degrades_without_raising(text, trained):
    prediction = run(
        classify_reply_cascade(
            text,
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=StubLLM(),
            model=trained,
        )
    )

    assert prediction.fallback_used is True


# --- wired into the service -------------------------------------------------


def test_installing_the_cascade_rebinds_the_primary_classifier(trained, monkeypatch):
    import src.ml.reply.cascade as cascade_module
    from src.ml.reply import service

    monkeypatch.setattr(cascade_module, "load_classifier", lambda *a, **k: (trained, None))
    install_cascade_classifier()

    assert service.primary_classifier is cascade_module.classify_reply_cascade


def test_the_full_pipeline_runs_on_the_cascade(trained, monkeypatch):
    """End to end: Stage C classifies, then the service adds entities and gating."""

    import src.ml.reply.cascade as cascade_module

    monkeypatch.setattr(cascade_module, "load_classifier", lambda *a, **k: (trained, None))
    install_cascade_classifier()

    prediction = run(
        understand_reply(
            "We have queued Rs. 45,000 for payment tomorrow.",
            "INV-1043",
            "rpl-1",
            client=StubLLM(),
            reference_dt=REFERENCE,
        )
    )

    assert prediction.intent is IntentLabel.PROMISE_TO_PAY
    assert prediction.model_used == MODEL_NAME
    assert prediction.fallback_used is False
    assert prediction.entities.promised_amount == 45000.0
    assert prediction.entities.promised_date == date(2026, 9, 2)
    assert prediction.entities.currency == "INR"


def test_an_escalated_reply_still_gets_its_entities(trained, monkeypatch):
    """The entity extractors run on the service's output, whichever stage answered.

    This phrasing is one Stage C reads correctly but is only ~0.44 confident
    about, so the cascade hands it to Stage A. The promise must still be
    extracted intact.
    """

    import src.ml.reply.cascade as cascade_module

    monkeypatch.setattr(cascade_module, "load_classifier", lambda *a, **k: (trained, None))
    install_cascade_classifier()

    stub = StubLLM(intent=IntentLabel.PROMISE_TO_PAY, confidence=0.94)
    prediction = run(
        understand_reply(
            "Will clear Rs. 45,000 by next Friday.",
            "INV-1043",
            "rpl-1",
            client=stub,
            reference_dt=REFERENCE,
        )
    )

    assert stub.calls == 1, "an under-confident Stage C read should reach Stage A"
    assert prediction.intent is IntentLabel.PROMISE_TO_PAY
    assert prediction.fallback_used is True
    assert prediction.fallback.resolved_by is FallbackResolver.LLM_FALLBACK
    assert prediction.entities.promised_amount == 45000.0
    assert prediction.entities.promised_date == date(2026, 9, 4)


def test_the_opt_out_guard_still_overrides_the_trained_model(trained, monkeypatch):
    """Stage C is a signal; an explicit stop-contact request is still binding.

    The classifier is forced to answer DISPUTE here on purpose. Asserting the
    override against the real model would only prove the model happened to get
    this sentence right, and would start failing the day it got better -- which
    is what this test did before. What must hold is that the deterministic
    guard wins whatever Stage C says, so Stage C is made to say the wrong
    thing.
    """

    import src.ml.reply.cascade as cascade_module

    class WrongClassifier:
        classes = trained.classes

        def predict_one(self, text):
            from src.ml.reply.classifier import IntentPrediction

            return IntentPrediction(
                intent=IntentLabel.DISPUTE,
                confidence=0.99,
                probabilities={IntentLabel.DISPUTE.value: 0.99},
                top_terms=[],
            )

    monkeypatch.setattr(
        cascade_module, "load_classifier", lambda *a, **k: (WrongClassifier(), None)
    )
    install_cascade_classifier()

    prediction = run(
        understand_reply(
            "The rate is wrong on this bill and do not contact me again.",
            "INV-1043",
            "rpl-1",
            client=StubLLM(),
            reference_dt=REFERENCE,
        )
    )

    assert prediction.intent is IntentLabel.OPT_OUT
    assert prediction.confidence == 1.0


def test_entities_are_still_gated_by_intent_under_the_cascade(trained, monkeypatch):
    import src.ml.reply.cascade as cascade_module

    monkeypatch.setattr(cascade_module, "load_classifier", lambda *a, **k: (trained, None))
    install_cascade_classifier()

    prediction = run(
        understand_reply(
            "Our office is closed for Diwali until next week.",
            "INV-1043",
            "rpl-1",
            client=StubLLM(intent=IntentLabel.OTHER),
            reference_dt=REFERENCE,
        )
    )

    assert prediction.entities.promised_date is None
    assert prediction.entities.promised_amount is None
