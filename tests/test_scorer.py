"""Tests for expected-value prioritization.

The scorer is not judged on accuracy here -- it is rules-based and says so.
What these tests hold it to is *ordering and honesty*: better-behaved customers
score higher, bigger exposure ranks higher, and every prediction admits it came
from a fallback rather than a trained model.
"""

from __future__ import annotations

import pytest

from app.core.scorer import (
    SCORER_VERSION,
    ScoringConfig,
    estimate_recovery_probability,
    rank_cases,
    score_case,
    urgency_weight,
)
from app.models.enums import InterventionTier, InvoiceStatus
from src.ml.schemas import FallbackResolver
from tests.conftest import make_case


class TestRecoveryProbability:
    def test_a_reliable_payer_scores_above_an_unreliable_one(self):
        reliable, _ = estimate_recovery_probability(
            make_case(on_time_ratio=0.95, avg_days_late=1.0)
        )
        unreliable, _ = estimate_recovery_probability(
            make_case(on_time_ratio=0.2, avg_days_late=35.0)
        )

        assert reliable > unreliable

    def test_broken_promises_lower_the_probability(self):
        clean, _ = estimate_recovery_probability(make_case(broken_promises=0))
        broken, _ = estimate_recovery_probability(make_case(broken_promises=10, invoice_count=10))

        assert broken < clean

    def test_broken_promises_are_read_as_a_rate_not_a_count(self):
        """Tenure must not be punished.

        Two customers with the same *proportion* of broken promises should
        score alike, even when one has ten times the history. Scoring on raw
        counts would make long-standing customers look worse than new ones by
        construction.
        """

        small, _ = estimate_recovery_probability(make_case(broken_promises=1, invoice_count=10))
        large, _ = estimate_recovery_probability(make_case(broken_promises=10, invoice_count=100))

        assert small == pytest.approx(large, abs=0.02)

    def test_ageing_lowers_the_probability(self):
        fresh, _ = estimate_recovery_probability(make_case(days_overdue=3))
        stale, _ = estimate_recovery_probability(make_case(days_overdue=90))

        assert stale < fresh

    def test_an_outsized_invoice_is_harder_to_collect(self):
        normal = make_case(amount=100_000.0)
        outsized = make_case(amount=100_000.0)
        outsized = outsized.model_copy(
            update={
                "customer": outsized.customer.model_copy(update={"avg_invoice_amount": 10_000.0})
            }
        )

        assert estimate_recovery_probability(outsized)[0] < estimate_recovery_probability(normal)[0]

    def test_probability_stays_in_range_under_extremes(self):
        extremes = [
            make_case(on_time_ratio=1.0, avg_days_late=0.0, days_overdue=0),
            make_case(
                on_time_ratio=0.0,
                avg_days_late=365.0,
                days_overdue=3650,
                broken_promises=999,
                invoice_count=1,
            ),
        ]

        for case in extremes:
            probability, _ = estimate_recovery_probability(case)
            assert 0.0 <= probability <= 1.0

    def test_drivers_are_returned_sorted_by_absolute_contribution(self):
        _, drivers = estimate_recovery_probability(make_case())

        magnitudes = [abs(driver.shap_contribution) for driver in drivers]
        assert magnitudes == sorted(magnitudes, reverse=True)


class TestUrgency:
    def test_urgency_rises_with_age_and_saturates(self):
        assert urgency_weight(0) < urgency_weight(10) < urgency_weight(40)
        assert urgency_weight(200) - urgency_weight(120) < 0.05

    def test_urgency_is_bounded(self):
        for days in (0, 1, 15, 60, 400):
            assert 0.0 <= urgency_weight(days) <= 1.0


class TestTiers:
    def test_a_near_certain_payer_is_left_alone(self):
        """The false-intervention guard.

        Contacting a customer who is about to pay is a cost, not a win, and the
        report counts it as one.
        """

        config = ScoringConfig(self_cure_probability=0.6)
        score = score_case(make_case(on_time_ratio=0.99, avg_days_late=0.5, days_overdue=2), config)

        assert score.tier is InterventionTier.WAIT
        assert "self-cure" in score.rationale

    def test_a_large_at_risk_invoice_escalates(self):
        score = score_case(
            make_case(
                amount=2_000_000.0,
                days_overdue=45,
                on_time_ratio=0.25,
                avg_days_late=30.0,
                broken_promises=8,
            )
        )

        assert score.tier is InterventionTier.ESCALATE

    def test_a_trivial_invoice_is_not_worth_chasing(self):
        score = score_case(make_case(amount=6_000.0, days_overdue=4))

        assert score.tier is InterventionTier.WAIT
        assert "threshold" in score.rationale

    def test_a_paid_invoice_is_never_actioned(self):
        score = score_case(make_case(status=InvoiceStatus.PAID, amount=5_000_000.0))

        assert score.tier is InterventionTier.WAIT
        assert "PAID" in score.rationale

    def test_outstanding_not_face_value_drives_the_decision(self):
        """A part-paid invoice is worth less attention than an untouched one."""

        full = score_case(make_case(amount=500_000.0, days_overdue=30))
        mostly_paid = score_case(
            make_case(amount=500_000.0, days_overdue=30, amount_paid=495_000.0)
        )

        assert mostly_paid.expected_value < full.expected_value
        assert mostly_paid.outstanding == 5_000.0

    def test_thresholds_are_configurable(self):
        case = make_case(amount=300_000.0, days_overdue=30, on_time_ratio=0.4)

        eager = score_case(case, ScoringConfig(escalate_threshold=1.0))
        cautious = score_case(case, ScoringConfig(escalate_threshold=10_000_000.0))

        assert eager.tier is InterventionTier.ESCALATE
        assert cautious.tier is not InterventionTier.ESCALATE


class TestHonesty:
    def test_every_prediction_declares_it_used_the_fallback_scorer(self):
        """Until Phase 4 ships a trained model, nothing may imply one ran."""

        score = score_case(make_case())

        assert score.prediction.fallback_used is True
        assert score.prediction.fallback is not None
        assert score.prediction.fallback.resolved_by is FallbackResolver.RULES_BASED_SCORER
        assert score.prediction.calibrated is False
        assert score.prediction.model_version == SCORER_VERSION

    def test_every_score_carries_a_human_readable_reason(self):
        for case in (make_case(), make_case(amount=5_000.0), make_case(days_overdue=90)):
            assert len(score_case(case).rationale) > 20


class TestRanking:
    def test_cases_come_back_highest_value_first(self):
        cases = [
            make_case(invoice_id="small", amount=20_000.0, days_overdue=5),
            make_case(
                invoice_id="huge",
                amount=3_000_000.0,
                days_overdue=60,
                on_time_ratio=0.2,
                avg_days_late=40.0,
            ),
            make_case(invoice_id="mid", amount=250_000.0, days_overdue=20),
        ]

        ranked = rank_cases(cases)

        assert [score.invoice_id for score in ranked][0] == "huge"
        values = [score.expected_value for score in ranked]
        assert values == sorted(values, reverse=True)

    def test_ranking_is_deterministic(self):
        cases = [make_case(invoice_id=f"INV-{i}", amount=10_000.0 * (i + 1)) for i in range(10)]

        assert [s.invoice_id for s in rank_cases(cases)] == [
            s.invoice_id for s in rank_cases(cases)
        ]
