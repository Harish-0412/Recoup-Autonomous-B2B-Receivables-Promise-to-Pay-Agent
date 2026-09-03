from datetime import timedelta

import pytest

from app.core.domain import CaseSnapshot, snapshot_from_generated
from src.data.synthetic_generator import generate_batch
from src.ml.features.recovery_features import (
    FEATURE_COLUMNS_V1,
    FEATURE_SET_VERSION,
    NO_PRIOR_PROMISE,
    build_recovery_features,
    feature_vector,
    recency_weighted_on_time_score,
)

FORBIDDEN = ("recovered", "recovered_date", "archetype", "true_recovery_probability")


@pytest.fixture(scope="module")
def batch():
    return generate_batch(batch_size=400, seed=42, timeline_days=540)


@pytest.fixture(scope="module")
def cases(batch):
    lookup = {customer.customer_id: customer for customer in batch.customers}
    return [snapshot_from_generated(inv, lookup[inv.customer_id]) for inv in batch.invoices]


# --- the declared feature set and the produced one cannot drift -------------


def test_feature_columns_are_unique_and_versioned():
    assert len(FEATURE_COLUMNS_V1) == len(set(FEATURE_COLUMNS_V1))
    assert FEATURE_SET_VERSION.endswith("v1")


def test_every_declared_column_is_populated(cases):
    features = build_recovery_features(cases[0])

    assert set(features) == set(FEATURE_COLUMNS_V1)
    assert all(isinstance(value, float) for value in features.values())


def test_feature_vector_follows_the_declared_order(cases):
    features = build_recovery_features(cases[0])
    vector = feature_vector(cases[0])

    assert vector == [features[column] for column in FEATURE_COLUMNS_V1]


def test_declared_and_produced_drift_is_caught(monkeypatch, cases):
    """Editing V1 in place must fail loudly, not silently mismatch at predict."""

    import src.ml.features.recovery_features as module

    monkeypatch.setattr(module, "FEATURE_COLUMNS_V1", FEATURE_COLUMNS_V1 + ("invented",))

    with pytest.raises(RuntimeError, match="feature set drift"):
        module.build_recovery_features(cases[0])


# --- no leakage -------------------------------------------------------------


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_ground_truth_never_appears_as_a_feature(cases, forbidden):
    features = build_recovery_features(cases[0])

    assert forbidden not in features
    assert not any(forbidden in column for column in features)


def test_the_snapshot_layer_carries_no_ground_truth(cases):
    """Leakage would have to be written on purpose, not forgotten into place."""

    dumped = cases[0].model_dump()

    text = str(dumped)
    for forbidden in FORBIDDEN:
        assert forbidden not in text


def test_features_are_identical_for_two_invoices_with_opposite_outcomes(batch):
    """Two identical inputs must produce identical features whatever happened next."""

    lookup = {customer.customer_id: customer for customer in batch.customers}
    recovered = next(inv for inv in batch.invoices if inv.recovered)
    twin = recovered.model_copy(deep=True)
    twin.recovered = not recovered.recovered
    twin.recovered_date = None

    customer = lookup[recovered.customer_id]
    first = build_recovery_features(snapshot_from_generated(recovered, customer))
    second = build_recovery_features(snapshot_from_generated(twin, customer))

    assert first == second


# --- train/serve parity -----------------------------------------------------


def _training_path(case: CaseSnapshot) -> dict[str, float]:
    """Stand-in for the offline path: iterate history, score as of the flag date."""

    return build_recovery_features(case, case.invoice.as_of)


def _inference_path(case: CaseSnapshot) -> dict[str, float]:
    """Stand-in for the live path: score the case as it arrives."""

    return build_recovery_features(case)


def test_training_and_inference_paths_produce_identical_features(cases):
    """The concrete, automated proof of train/serve parity."""

    for case in cases[:200]:
        assert _training_path(case) == _inference_path(case), case.invoice.invoice_id


def test_the_function_is_deterministic(cases):
    case = cases[0]

    assert build_recovery_features(case) == build_recovery_features(case)


def test_as_of_actually_moves_the_time_dependent_features(cases):
    case = cases[0]
    later = case.invoice.as_of + timedelta(days=10)

    now = build_recovery_features(case)
    future = build_recovery_features(case, later)

    assert future["days_overdue_at_scoring"] == now["days_overdue_at_scoring"] + 10
    assert future["invoice_age_days"] == now["invoice_age_days"] + 10
    # Customer history is not a function of the scoring instant.
    assert future["customer_on_time_ratio_90d"] == now["customer_on_time_ratio_90d"]


def test_days_overdue_never_goes_negative(cases):
    case = cases[0]
    before_due = case.invoice.due_date - timedelta(days=5)

    features = build_recovery_features(case, before_due)

    assert features["days_overdue_at_scoring"] == 0.0


# --- individual feature semantics -------------------------------------------


def test_absent_prior_promise_is_neutral_not_broken(cases):
    case = next(c for c in cases if not c.invoice.has_prior_promise)

    features = build_recovery_features(case)

    assert features["has_prior_promise"] == 0.0
    assert features["prior_promise_kept"] == NO_PRIOR_PROMISE


def test_a_kept_prior_promise_reads_as_one(cases):
    case = next(
        (c for c in cases if c.invoice.has_prior_promise and c.invoice.prior_promise_kept),
        None,
    )
    assert case is not None

    assert build_recovery_features(case)["prior_promise_kept"] == 1.0


def test_never_contacted_sentinel_is_capped(cases):
    case = cases[0].model_copy(deep=True)
    case.invoice.days_since_last_contact = 9_999

    features = build_recovery_features(case)

    assert features["days_since_last_contact"] == 180.0


def test_size_ratio_compares_the_invoice_to_the_customers_norm(cases):
    case = cases[0].model_copy(deep=True)
    case.customer.avg_invoice_amount = 100_000.0
    case.invoice.amount = 250_000.0

    features = build_recovery_features(case)

    assert features["invoice_amount_vs_customer_avg_ratio"] == pytest.approx(2.5)


def test_size_ratio_survives_a_customer_with_no_history(cases):
    case = cases[0].model_copy(deep=True)
    case.customer.avg_invoice_amount = 0.0

    features = build_recovery_features(case)

    assert features["invoice_amount_vs_customer_avg_ratio"] <= 50.0


# --- the recency-weighted score ---------------------------------------------


def test_recency_weighting_leans_on_recent_behaviour_for_established_customers():
    established = recency_weighted_on_time_score(0.2, 0.9, invoice_count=80)
    new = recency_weighted_on_time_score(0.2, 0.9, invoice_count=1)

    # With plenty of history the recent collapse dominates; with almost none
    # the recent figure is mostly noise and the long-run one should lead.
    assert established < new


def test_recency_weighting_is_bounded_by_its_inputs():
    for count in (0, 3, 20, 100):
        score = recency_weighted_on_time_score(0.3, 0.8, invoice_count=count)
        assert 0.3 <= score <= 0.8


def test_recency_weighting_agrees_when_both_ratios_agree():
    assert recency_weighted_on_time_score(0.7, 0.7, invoice_count=25) == pytest.approx(0.7)


def test_risk_escalating_customers_score_below_their_all_time_ratio(batch):
    """The feature has to be able to see a customer who is getting worse."""

    from src.data.synthetic_generator import CustomerArchetype

    escalating = [c for c in batch.customers if c.archetype is CustomerArchetype.RISK_ESCALATING]
    assert escalating

    gaps = [
        c.on_time_ratio_all_time
        - recency_weighted_on_time_score(
            c.on_time_ratio_90d, c.on_time_ratio_all_time, c.invoice_count
        )
        for c in escalating
    ]
    assert sum(gaps) / len(gaps) > 0.10
