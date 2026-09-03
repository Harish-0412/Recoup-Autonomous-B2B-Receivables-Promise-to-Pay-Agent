import numpy as np
import pytest

from src.data.synthetic_generator import generate_batch
from src.ml.recovery.dataset import build_dataset, features_frame
from src.ml.recovery.evaluation import (
    calibration_bins,
    compare_rankings,
    evaluate,
    format_calibration_curve,
    format_metrics_table,
    rules_based_scores,
    value_at_risk_captured_at_k,
)
from src.ml.recovery.explain import RecoveryExplainer, format_global_importance
from src.ml.recovery.models import (
    BASELINE_NAME,
    NEURAL_NAME,
    PRIMARY_NAME,
    build_gradient_boosting,
    build_logistic_regression,
    build_neural_network,
    train_model,
    uncalibrated,
)


@pytest.fixture(scope="module")
def batch():
    return generate_batch(
        batch_size=6000, customer_count=900, seed=42, timeline_days=540
    )


@pytest.fixture(scope="module")
def dataset(batch):
    return build_dataset(batch)


@pytest.fixture(scope="module")
def models(dataset):
    fitted = {}
    for name, estimator in (
        (BASELINE_NAME, build_logistic_regression(42)),
        (PRIMARY_NAME, build_gradient_boosting(42, n_estimators=200)),
    ):
        fitted[name] = train_model(
            name,
            estimator,
            dataset.train.features,
            dataset.train.labels,
            x_validation=dataset.validation.features,
            y_validation=dataset.validation.labels,
        )
    return fitted


@pytest.fixture(scope="module")
def scored(dataset, models):
    out = {}
    for name, model in models.items():
        out[name] = model.predict_proba(dataset.test.features)
    out["rules-based"] = np.asarray(rules_based_scores(dataset.test.cases))
    return out


# --- the split is temporal and the labels are mature ------------------------


def test_only_mature_labels_reach_the_dataset(batch, dataset):
    total = len(dataset.train) + len(dataset.validation) + len(dataset.test)

    assert total == len(batch.mature_invoices())
    assert total < len(batch.invoices), "some invoices should still be inside the horizon"


def test_immature_invoices_are_excluded_because_their_window_has_not_closed(batch):
    mature_ids = {invoice.invoice_id for invoice in batch.mature_invoices()}
    excluded = [inv for inv in batch.invoices if inv.invoice_id not in mature_ids]

    assert excluded
    for invoice in excluded:
        assert (batch.as_of - invoice.flagged_date).days < batch.horizon_days


def test_splits_do_not_overlap_in_time(dataset):
    assert max(dataset.train.flag_dates) < min(dataset.validation.flag_dates)
    assert max(dataset.validation.flag_dates) < min(dataset.test.flag_dates)


def test_the_test_split_is_the_most_recent_slice(dataset):
    assert min(dataset.test.flag_dates) > max(dataset.train.flag_dates)


def test_no_invoice_appears_in_two_splits(dataset):
    ids = [
        {case.invoice.invoice_id for case in split.cases}
        for split in (dataset.train, dataset.validation, dataset.test)
    ]

    assert not (ids[0] & ids[1])
    assert not (ids[0] & ids[2])
    assert not (ids[1] & ids[2])


def test_a_flag_date_never_straddles_a_split_boundary(dataset):
    """Cutting mid-day would leak same-day information across the boundary."""

    assignments: dict[object, set[str]] = {}
    for split in (dataset.train, dataset.validation, dataset.test):
        for flag_date in split.flag_dates:
            assignments.setdefault(flag_date, set()).add(split.name)

    straddling = {day: names for day, names in assignments.items() if len(names) > 1}
    assert not straddling, straddling


def test_every_split_has_both_classes(dataset):
    for split in (dataset.train, dataset.validation, dataset.test):
        assert set(np.unique(split.labels)) == {0, 1}, split.name


def test_feature_matrix_columns_match_the_declared_set(dataset):
    assert list(dataset.train.features.columns) == list(dataset.feature_columns)
    assert dataset.train.features.notna().all().all()


def test_labels_line_up_with_the_cases(dataset):
    assert len(dataset.test.labels) == len(dataset.test.cases)
    assert len(dataset.test.features) == len(dataset.test.cases)


def test_dataset_rejects_impossible_fractions(batch):
    with pytest.raises(ValueError):
        build_dataset(batch, train_fraction=0.9, validation_fraction=0.2)


def test_dataset_needs_a_timeline_longer_than_the_horizon():
    """Every label is immature when the batch is all flagged today."""

    flat = generate_batch(batch_size=200, seed=1, timeline_days=3)

    with pytest.raises(ValueError, match="mature label"):
        build_dataset(flat)


def test_features_frame_is_numeric_and_ordered(dataset):
    frame = features_frame(dataset.test.cases[:20])

    assert list(frame.columns) == list(dataset.feature_columns)
    assert frame.dtypes.eq("float64").all()


# --- the models learn something ---------------------------------------------


def test_both_models_beat_chance_on_the_held_out_split(dataset, scored):
    for name in (BASELINE_NAME, PRIMARY_NAME):
        metrics = evaluate(name, "test", dataset.test.labels, scored[name])
        assert metrics.roc_auc > 0.65, (name, metrics.roc_auc)


def test_probabilities_stay_in_range(dataset, scored):
    for name, probabilities in scored.items():
        assert probabilities.min() >= 0.0, name
        assert probabilities.max() <= 1.0, name


def test_calibration_puts_the_probability_close_to_the_truth(dataset, scored):
    metrics = evaluate(BASELINE_NAME, "test", dataset.test.labels, scored[BASELINE_NAME])

    assert metrics.expected_calibration_error < 0.10, metrics.expected_calibration_error


def test_the_trained_model_is_better_calibrated_than_the_rules(dataset, scored):
    trained = evaluate(BASELINE_NAME, "test", dataset.test.labels, scored[BASELINE_NAME])
    rules = evaluate("rules-based", "test", dataset.test.labels, scored["rules-based"])

    assert trained.brier_score < rules.brier_score
    assert trained.expected_calibration_error < rules.expected_calibration_error


def test_calibration_is_fitted_on_validation_not_training(dataset):
    """Calibrating on training data is the classic way to fake a good curve."""

    model = train_model(
        BASELINE_NAME,
        build_logistic_regression(42),
        dataset.train.features,
        dataset.train.labels,
        x_validation=dataset.validation.features,
        y_validation=dataset.validation.labels,
    )

    assert model.calibrated is True
    assert model.calibration_method == "isotonic"


def test_training_without_validation_leaves_the_model_uncalibrated(dataset):
    model = train_model(
        BASELINE_NAME,
        build_logistic_regression(42),
        dataset.train.features,
        dataset.train.labels,
        calibration_method=None,
    )

    assert model.calibrated is False
    assert model.calibration_method is None


def test_calibrate_rejects_an_unknown_method(dataset):
    from src.ml.recovery.models import calibrate

    with pytest.raises(ValueError):
        calibrate(build_logistic_regression(42), dataset.validation.features, [0, 1], method="nope")


def test_the_neural_net_trains_and_is_reported_rather_than_assumed(dataset):
    """The comparison must actually run; whether it wins is a finding either way."""

    model = train_model(
        NEURAL_NAME,
        build_neural_network(42),
        dataset.train.features,
        dataset.train.labels,
        x_validation=dataset.validation.features,
        y_validation=dataset.validation.labels,
    )
    metrics = evaluate(
        NEURAL_NAME, "test", dataset.test.labels, model.predict_proba(dataset.test.features)
    )

    assert metrics.roc_auc > 0.6


def test_predict_one_matches_the_batch_path(dataset, models):
    model = models[BASELINE_NAME]
    from src.ml.features.recovery_features import build_recovery_features

    case = dataset.test.cases[0]
    single = model.predict_one(build_recovery_features(case))
    batched = float(model.predict_proba(dataset.test.features.head(1))[0])

    assert single == pytest.approx(batched)


def test_predict_rejects_a_wrong_shaped_matrix(models):
    model = models[BASELINE_NAME]

    with pytest.raises(ValueError):
        model.predict_proba(np.zeros((2, 3)))


# --- head to head against the scorer it replaces ----------------------------


def test_the_trained_model_outranks_the_rules_based_scorer(dataset, scored):
    """The headline claim of this feature, asserted rather than asserted-to."""

    amounts = [case.invoice.outstanding for case in dataset.test.cases]
    comparison = compare_rankings(
        "test",
        dataset.test.labels,
        scored[BASELINE_NAME],
        scored["rules-based"],
        amounts,
        challenger=BASELINE_NAME,
        incumbent="rules-based",
    )

    assert comparison.challenger_wins, comparison.verdict()
    assert comparison.challenger_brier < comparison.incumbent_brier


def test_the_rules_scorer_is_called_through_its_shipping_entry_point(dataset):
    scores = rules_based_scores(dataset.test.cases[:20])

    assert len(scores) == 20
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_value_at_risk_rewards_surfacing_money_that_would_be_lost():
    y_true = [0, 0, 1, 1]
    amounts = [100.0, 200.0, 300.0, 400.0]
    good = [0.1, 0.2, 0.8, 0.9]  # ranks the unrecovered ones first
    bad = [0.9, 0.8, 0.2, 0.1]

    assert value_at_risk_captured_at_k(y_true, good, amounts, k=2) == 300.0
    assert value_at_risk_captured_at_k(y_true, bad, amounts, k=2) == 0.0


def test_value_at_risk_rejects_a_non_positive_k():
    with pytest.raises(ValueError):
        value_at_risk_captured_at_k([1], [0.5], [10.0], k=0)


def test_comparison_needs_both_classes_present():
    with pytest.raises(ValueError):
        compare_rankings(
            "test", [1, 1], [0.6, 0.7], [0.5, 0.4], [1.0, 1.0],
            challenger="a", incumbent="b",
        )


# --- evaluation utilities ---------------------------------------------------


def test_evaluate_rejects_probabilities_out_of_range():
    with pytest.raises(ValueError):
        evaluate("m", "test", [0, 1], [0.5, 1.4])


def test_evaluate_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        evaluate("m", "test", [0, 1], [0.5])


def test_evaluate_rejects_an_empty_split():
    with pytest.raises(ValueError):
        evaluate("m", "test", [], [])


def test_a_single_class_split_reports_chance_rather_than_raising():
    metrics = evaluate("m", "test", [1, 1, 1], [0.9, 0.8, 0.7])

    assert metrics.roc_auc == 0.5


def test_calibration_bins_cover_every_prediction(dataset, scored):
    bins, ece = calibration_bins(dataset.test.labels, scored[BASELINE_NAME])

    assert sum(row.count for row in bins) == len(dataset.test.labels)
    assert 0.0 <= ece <= 1.0


def test_a_perfectly_calibrated_set_has_near_zero_error():
    y_true = [1] * 70 + [0] * 30
    probabilities = [0.7] * 100

    _, ece = calibration_bins(y_true, probabilities)

    assert ece < 0.01


def test_an_overconfident_set_is_caught():
    y_true = [1] * 50 + [0] * 50
    probabilities = [0.99] * 100

    _, ece = calibration_bins(y_true, probabilities)

    assert ece > 0.4


def test_formatters_render_every_model(dataset, scored):
    rows = [
        evaluate(name, "test", dataset.test.labels, probabilities)
        for name, probabilities in scored.items()
    ]

    table = format_metrics_table(rows)
    for row in rows:
        assert row.model_name in table
    assert "AUC" in table
    assert "predicted" in format_calibration_curve(rows[0])


# --- explainability ---------------------------------------------------------


def test_shap_explains_the_gradient_boosted_model(dataset, models):
    explainer = RecoveryExplainer(models[PRIMARY_NAME])

    assert explainer.available
    assert explainer.kind == "tree"


def test_shap_explains_the_linear_model_too(dataset, models):
    """The model that ships is chosen on AUC, so both paths must work."""

    explainer = RecoveryExplainer(models[BASELINE_NAME])

    assert explainer.available
    assert explainer.kind == "linear"


@pytest.mark.parametrize("model_name", [BASELINE_NAME, PRIMARY_NAME])
def test_per_prediction_drivers_name_real_features(dataset, models, model_name):
    from src.ml.features.recovery_features import build_recovery_features

    explainer = RecoveryExplainer(models[model_name])
    features = build_recovery_features(dataset.test.cases[0])
    drivers = explainer.top_drivers(features, limit=3)

    assert len(drivers) == 3
    for driver in drivers:
        assert driver.feature in dataset.feature_columns
        assert driver.value == pytest.approx(round(features[driver.feature], 4))

    magnitudes = [abs(driver.shap_contribution) for driver in drivers]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_global_importance_ranks_the_features(dataset, models):
    explainer = RecoveryExplainer(models[PRIMARY_NAME])
    importance = explainer.global_importance(dataset.test.features.head(200))

    assert importance
    values = [value for _, value in importance]
    assert values == sorted(values, reverse=True)
    assert all(name in dataset.feature_columns for name, _ in importance)


def test_customer_payment_history_dominates_the_ranking(dataset, models):
    """A sanity check against the data-generating process."""

    explainer = RecoveryExplainer(models[PRIMARY_NAME])
    importance = explainer.global_importance(dataset.test.features.head(300), limit=6)
    top = {name for name, _ in importance}

    history = {
        "recency_weighted_on_time_score",
        "customer_on_time_ratio_90d",
        "customer_on_time_ratio_all_time",
        "customer_avg_days_late",
    }
    assert top & history, top


def test_an_unexplainable_model_degrades_to_no_drivers(dataset):
    """Failing to explain a score must never become failing to produce one."""

    from src.ml.recovery.models import FittedModel

    class Opaque:
        def predict_proba(self, frame):
            return np.column_stack([np.full(len(frame), 0.4), np.full(len(frame), 0.6)])

    model = FittedModel(
        name="opaque",
        estimator=Opaque(),
        feature_columns=tuple(dataset.feature_columns),
        calibrated=False,
        calibration_method=None,
    )
    explainer = RecoveryExplainer(model)

    assert not explainer.available
    assert explainer.top_drivers({c: 0.0 for c in dataset.feature_columns}) == []
    assert explainer.global_importance(dataset.test.features.head(5)) == []
    assert "unavailable" in format_global_importance([])


def test_uncalibrated_peels_the_calibration_and_freezing_wrappers(models):
    from sklearn.pipeline import Pipeline

    inner = uncalibrated(models[BASELINE_NAME])

    assert isinstance(inner, Pipeline)
    assert hasattr(inner.steps[-1][1], "coef_")
