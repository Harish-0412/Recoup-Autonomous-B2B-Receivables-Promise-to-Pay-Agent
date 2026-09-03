"""Tests for promise-to-pay tracking.

The invariant under test throughout: **a promise is evidence of intent, not
evidence of payment.** Nothing a customer says can mark an invoice paid, and
nothing the classifier reports at low confidence becomes a tracked commitment
without a human seeing it.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.promise_tracker import (
    MAX_PROMISE_HORIZON_DAYS,
    MIN_PROMISE_CONFIDENCE,
    PromiseRecord,
    PromiseRejected,
    assess_promise,
    extract_promise,
    supersede,
)
from app.models.enums import DecisionOutcome, PromiseStatus
from src.ml.schemas import (
    ExtractedEntities,
    FallbackReason,
    FallbackResult,
    IntentLabel,
    ReplyIntentPrediction,
)

TODAY = date(2026, 9, 1)


def prediction(
    *,
    intent: IntentLabel = IntentLabel.PROMISE_TO_PAY,
    confidence: float = 0.9,
    amount: float | None = 45_000.0,
    promised: date | None = date(2026, 9, 10),
    fallback: bool = False,
) -> ReplyIntentPrediction:
    return ReplyIntentPrediction(
        reply_id="R-1",
        invoice_id="INV-1",
        raw_text="I will pay by Friday",
        intent=intent,
        entities=ExtractedEntities(promised_amount=amount, promised_date=promised),
        model_used="test",
        model_version="test-v1",
        confidence=confidence,
        fallback_used=fallback,
        fallback=(
            FallbackResult(triggered=True, reason=FallbackReason.LOW_CONFIDENCE)
            if fallback
            else None
        ),
    )


class TestExtraction:
    def test_a_clear_promise_is_recorded(self, ledger):
        result = extract_promise(prediction(), invoice_amount=50_000.0, as_of=TODAY, ledger=ledger)

        assert isinstance(result, PromiseRecord)
        assert result.promised_amount == 45_000.0
        assert result.promised_date == date(2026, 9, 10)
        assert result.status is PromiseStatus.PENDING

    def test_a_promise_with_no_amount_means_the_whole_invoice(self, ledger):
        result = extract_promise(
            prediction(amount=None), invoice_amount=50_000.0, as_of=TODAY, ledger=ledger
        )

        assert isinstance(result, PromiseRecord)
        assert result.promised_amount == 50_000.0

    def test_a_promise_larger_than_the_invoice_is_clamped(self, ledger):
        """An over-large figure is an extraction error, not a windfall."""

        result = extract_promise(
            prediction(amount=9_999_999.0),
            invoice_amount=50_000.0,
            as_of=TODAY,
            ledger=ledger,
        )

        assert isinstance(result, PromiseRecord)
        assert result.promised_amount == 50_000.0

    @pytest.mark.parametrize(
        "intent",
        [
            IntentLabel.DISPUTE,
            IntentLabel.OPT_OUT,
            IntentLabel.GENERAL_QUERY,
            IntentLabel.OTHER,
        ],
    )
    def test_non_promise_intents_never_create_a_promise(self, intent, ledger):
        result = extract_promise(
            prediction(intent=intent), invoice_amount=50_000.0, as_of=TODAY, ledger=ledger
        )

        assert isinstance(result, PromiseRejected)
        assert result.code == "intent_not_a_promise"

    def test_low_confidence_goes_to_a_human_instead(self, ledger):
        result = extract_promise(
            prediction(confidence=MIN_PROMISE_CONFIDENCE - 0.01),
            invoice_amount=50_000.0,
            as_of=TODAY,
            ledger=ledger,
        )

        assert isinstance(result, PromiseRejected)
        assert result.code == "low_confidence"

    def test_a_fallback_prediction_never_creates_a_promise(self, ledger):
        """Silencing the agent on a degraded classification would be the worst
        of both worlds: no chase, and no human looking either."""

        result = extract_promise(
            prediction(fallback=True, confidence=0.99),
            invoice_amount=50_000.0,
            as_of=TODAY,
            ledger=ledger,
        )

        assert isinstance(result, PromiseRejected)
        assert result.code == "low_confidence"

    def test_a_promise_without_a_date_is_not_trackable(self, ledger):
        result = extract_promise(
            prediction(promised=None), invoice_amount=50_000.0, as_of=TODAY, ledger=ledger
        )

        assert isinstance(result, PromiseRejected)
        assert result.code == "no_date"

    def test_a_date_in_the_past_is_rejected(self, ledger):
        result = extract_promise(
            prediction(promised=date(2026, 8, 1)),
            invoice_amount=50_000.0,
            as_of=TODAY,
            ledger=ledger,
        )

        assert isinstance(result, PromiseRejected)
        assert result.code == "date_in_past"

    def test_an_implausibly_distant_date_goes_to_a_human(self, ledger):
        result = extract_promise(
            prediction(promised=date(2028, 1, 1)),
            invoice_amount=50_000.0,
            as_of=TODAY,
            ledger=ledger,
        )

        assert isinstance(result, PromiseRejected)
        assert result.code == "date_too_far"
        assert str(MAX_PROMISE_HORIZON_DAYS) in result.message

    def test_every_rejection_is_audited(self, ledger):
        extract_promise(
            prediction(intent=IntentLabel.DISPUTE),
            invoice_amount=50_000.0,
            as_of=TODAY,
            ledger=ledger,
        )

        (entry,) = ledger.entries()
        assert entry.event == "promise:not_recorded"
        assert entry.outcome is DecisionOutcome.SKIPPED
        assert entry.payload["code"] == "intent_not_a_promise"


class TestAssessment:
    def make_promise(self) -> PromiseRecord:
        return PromiseRecord(
            invoice_id="INV-1",
            promised_amount=45_000.0,
            promised_date=date(2026, 9, 10),
        )

    def test_a_promise_is_pending_before_its_date(self, ledger):
        outcome = assess_promise(
            self.make_promise(), amount_paid=0.0, as_of=date(2026, 9, 5), ledger=ledger
        )

        assert outcome.status is PromiseStatus.PENDING
        assert outcome.should_escalate is False

    def test_the_grace_period_is_honoured(self, ledger):
        """A payment settling a day late must not trigger an escalation."""

        outcome = assess_promise(
            self.make_promise(),
            amount_paid=0.0,
            as_of=date(2026, 9, 11),
            grace_days=2,
            ledger=ledger,
        )

        assert outcome.status is PromiseStatus.PENDING

    def test_a_promise_breaks_after_its_date_and_grace(self, ledger):
        outcome = assess_promise(
            self.make_promise(), amount_paid=0.0, as_of=date(2026, 9, 20), ledger=ledger
        )

        assert outcome.status is PromiseStatus.BROKEN
        assert outcome.should_escalate is True

    def test_payment_keeps_the_promise(self, ledger):
        outcome = assess_promise(
            self.make_promise(),
            amount_paid=45_000.0,
            as_of=date(2026, 9, 20),
            ledger=ledger,
        )

        assert outcome.status is PromiseStatus.KEPT
        assert outcome.should_escalate is False

    def test_payment_settles_a_promise_even_before_its_date(self, ledger):
        outcome = assess_promise(
            self.make_promise(),
            amount_paid=50_000.0,
            as_of=date(2026, 9, 2),
            ledger=ledger,
        )

        assert outcome.status is PromiseStatus.KEPT

    def test_a_short_payment_does_not_keep_the_promise(self, ledger):
        outcome = assess_promise(
            self.make_promise(),
            amount_paid=44_000.0,
            as_of=date(2026, 9, 20),
            ledger=ledger,
        )

        assert outcome.status is PromiseStatus.BROKEN

    def test_an_already_resolved_promise_is_not_reassessed(self, ledger):
        kept = self.make_promise().model_copy(update={"status": PromiseStatus.KEPT})

        outcome = assess_promise(kept, amount_paid=0.0, as_of=date(2026, 12, 1), ledger=ledger)

        assert outcome.status is PromiseStatus.KEPT
        assert len(ledger) == 0

    def test_breaking_a_promise_is_audited(self, ledger):
        assess_promise(self.make_promise(), amount_paid=0.0, as_of=date(2026, 9, 20), ledger=ledger)

        (entry,) = ledger.entries()
        assert entry.event == "promise:broken"
        assert entry.outcome is DecisionOutcome.BLOCKED


def test_superseding_keeps_the_old_promise_as_history(ledger):
    """Renegotiation must not erase the pattern of renegotiation."""

    first = PromiseRecord(
        invoice_id="INV-1", promised_amount=45_000.0, promised_date=date(2026, 9, 10)
    )
    second = PromiseRecord(
        invoice_id="INV-1", promised_amount=45_000.0, promised_date=date(2026, 9, 25)
    )

    retired = supersede(first, second, ledger=ledger)

    assert retired.status is PromiseStatus.SUPERSEDED
    assert retired.promise_id == first.promise_id
    assert ledger.entries()[0].event == "promise:superseded"
