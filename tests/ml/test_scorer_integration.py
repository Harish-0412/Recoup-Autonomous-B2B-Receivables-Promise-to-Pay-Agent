"""The seam between the agent loop and the recovery model.

The model was trainable and explainable well before it was *reachable*: for a
while ``score_case`` called the hand-written logistic directly, so a trained
artifact could sit on disk and change nothing the agent did. These tests cover
the wiring that closed that gap, and they are deliberately about routing rather
than about accuracy -- how good the model is belongs in
``test_recovery_model.py``.

Three properties are worth pinning down:

* passing a scorer actually changes the probability the formula uses;
* the *rest* of the expected-value formula is untouched by the swap, because
  replacing P(recovery) was the whole point of the exercise;
* a failing model degrades that one invoice to the rules rather than taking the
  batch down with it.
"""

import pytest

from app.core.agent import default_recovery_scorer, run_batch, run_cycle
from app.core.audit import DecisionLedger
from app.core.domain import snapshot_from_generated
from app.core.scorer import SCORER_VERSION, score_case, urgency_weight
from src.data.synthetic_generator import generate_batch
from src.ml.recovery.scorer import ModelBasedScorer, RulesBasedScorer


@pytest.fixture(scope="module")
def cases():
    batch = generate_batch(seed=404, batch_size=40)
    customers = {customer.customer_id: customer for customer in batch.customers}
    return [
        snapshot_from_generated(invoice, customers[invoice.customer_id])
        for invoice in batch.invoices
    ]


class _StubScorer:
    """A scorer that always returns ``probability``, to make routing visible."""

    name = "stub"

    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.calls = 0

    def score(self, case, as_of=None):
        self.calls += 1
        from src.ml.schemas import RecoveryScorePrediction
        from src.ml.versioning import utc_now

        return RecoveryScorePrediction(
            invoice_id=case.invoice.invoice_id,
            p_recovery_30d=self.probability,
            calibrated=True,
            top_drivers=[],
            model_version="stub-v1",
            confidence=1.0,
            fallback_used=False,
            fallback=None,
            scored_at=utc_now(),
        )


# --- routing ---------------------------------------------------------------


def test_no_scorer_keeps_the_hand_written_rules(cases):
    score = score_case(cases[0])
    assert score.prediction.model_version == SCORER_VERSION
    assert score.prediction.fallback_used is True


def test_a_supplied_scorer_replaces_the_probability(cases):
    stub = _StubScorer(0.11)
    score = score_case(cases[0], None, stub)

    assert stub.calls == 1
    assert score.p_recovery == pytest.approx(0.11)
    assert score.prediction.model_version == "stub-v1"


def test_the_rules_are_not_consulted_when_a_scorer_is_supplied(cases):
    """The two paths must not be averaged or blended -- one of them wins."""

    rules_probability = score_case(cases[0]).p_recovery
    supplied = score_case(cases[0], None, _StubScorer(0.11)).p_recovery

    assert supplied != pytest.approx(rules_probability)
    assert supplied == pytest.approx(0.11)


def test_only_the_probability_changes_not_the_formula(cases):
    """Swapping the scorer must not disturb urgency, cost or exposure."""

    case = cases[0]
    score = score_case(case, None, _StubScorer(0.25))

    weight = urgency_weight(case.invoice.days_overdue, horizon_days=30)
    expected = (1.0 - 0.25) * case.invoice.outstanding * weight - 250.0

    assert score.urgency_weight == pytest.approx(weight, abs=1e-4)
    assert score.expected_value == pytest.approx(expected, abs=0.01)
    assert score.outstanding == pytest.approx(round(case.invoice.outstanding, 2))


# --- the agent loop --------------------------------------------------------


def test_run_cycle_threads_the_scorer_through(cases):
    stub = _StubScorer(0.9)
    result = run_cycle(cases[0], scorer=stub, ledger=DecisionLedger())

    assert stub.calls >= 1
    assert result.score.prediction.model_version == "stub-v1"


def test_run_batch_scores_every_case_through_the_given_scorer(cases):
    stub = _StubScorer(0.5)
    results = run_batch(cases[:10], scorer=stub, ledger=DecisionLedger())

    assert len(results) == 10
    assert all(r.score.prediction.model_version == "stub-v1" for r in results)


def test_the_decision_trace_records_which_scorer_ran(cases):
    """Provenance is the point: a trace that omits it cannot be audited."""

    ledger = DecisionLedger()
    run_batch(cases[:5], scorer=_StubScorer(0.4), ledger=ledger)

    scored = [entry for entry in ledger.entries() if entry.event == "scored"]
    assert scored
    assert all(entry.payload["scorer_version"] == "stub-v1" for entry in scored)
    assert ledger.is_valid()


def test_the_default_scorer_is_built_once(cases):
    """Loading the artifact per invoice would be a real performance bug."""

    assert default_recovery_scorer() is default_recovery_scorer()


# --- fallback --------------------------------------------------------------


class _BrokenModel:
    name = "broken"
    calibrated = True
    feature_columns = ()
    estimator = None
    background = None

    def predict_one(self, features):
        raise RuntimeError("boom")


class _NanModel(_BrokenModel):
    def predict_one(self, features):
        return float("nan")


class _OutOfRangeModel(_BrokenModel):
    def predict_one(self, features):
        return 7.5


@pytest.mark.parametrize(
    ("model", "reason"),
    [
        (_BrokenModel(), "unexpected_error"),
        (_NanModel(), "invalid_output"),
        (_OutOfRangeModel(), "invalid_output"),
    ],
)
def test_a_failing_model_degrades_to_the_rules(cases, model, reason):
    prediction = ModelBasedScorer(model=model).score(cases[0])

    assert prediction.fallback_used is True
    assert prediction.fallback.reason.value == reason
    assert prediction.fallback.resolved_by.value == "rules_based_scorer"
    # The invoice still got a usable probability -- that is what keeps the
    # batch running rather than raising.
    assert 0.0 <= prediction.p_recovery_30d <= 1.0


def test_a_failing_model_yields_the_same_answer_the_rules_would(cases):
    degraded = ModelBasedScorer(model=_BrokenModel()).score(cases[0])
    rules = RulesBasedScorer().score(cases[0])

    assert degraded.p_recovery_30d == pytest.approx(rules.p_recovery_30d)


def test_a_missing_artifact_degrades_rather_than_raising(cases):
    prediction = ModelBasedScorer(model_version="no-such-model").score(cases[0])

    assert prediction.fallback_used is True
    assert prediction.fallback.reason.value == "model_load_failed"


def test_a_broken_model_does_not_stop_the_batch(cases):
    """The resilience claim, stated as a test rather than as a comment."""

    scorer = ModelBasedScorer(model=_BrokenModel())
    results = run_batch(cases[:12], scorer=scorer, ledger=DecisionLedger())

    assert len(results) == 12
    assert all(r.score.prediction.fallback_used for r in results)
