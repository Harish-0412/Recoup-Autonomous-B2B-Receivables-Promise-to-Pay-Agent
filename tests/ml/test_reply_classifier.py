import numpy as np
import pytest

from src.ml.config import MLSettings
from src.ml.reply.classifier import (
    MODEL_NAME,
    ReplyIntentClassifier,
    classifier_is_available,
    clear_classifier_cache,
    load_classifier,
    save_classifier,
)
from src.ml.reply.dataset import SplitName, build_corpus, corpus_texts_and_labels
from src.ml.reply.evaluation import (
    calibration_report,
    cascade_report,
    classification_report,
    entity_report,
    format_classification_report,
    format_confusion_matrix,
)
from src.ml.schemas import IntentLabel


@pytest.fixture(scope="module")
def corpus():
    return build_corpus(size=1200, seed=42)


@pytest.fixture(scope="module")
def trained(corpus):
    texts, labels = corpus_texts_and_labels(corpus.for_split(SplitName.TRAIN))
    return ReplyIntentClassifier.fit(texts, labels, seed=42)


@pytest.fixture(scope="module")
def test_predictions(corpus, trained):
    texts, labels = corpus_texts_and_labels(corpus.for_split(SplitName.TEST))
    probabilities = trained.predict_proba(texts)
    classes = trained.classes
    predicted = [classes[int(row.argmax())] for row in probabilities]
    confidences = [float(row.max()) for row in probabilities]
    return labels, predicted, confidences


@pytest.fixture(autouse=True)
def clear_cache():
    clear_classifier_cache()
    yield
    clear_classifier_cache()


# --- fitting ----------------------------------------------------------------


def test_fit_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        ReplyIntentClassifier.fit(["a", "b"], ["PROMISE_TO_PAY"])


def test_fit_rejects_an_empty_corpus():
    with pytest.raises(ValueError):
        ReplyIntentClassifier.fit([], [])


def test_classifier_learns_all_eight_classes(trained):
    assert set(trained.classes) == {label.value for label in IntentLabel}


def test_probabilities_are_a_distribution(trained):
    probabilities = trained.predict_proba(["Will clear 45,000 on Friday."])

    assert probabilities.shape == (1, 8)
    assert np.isclose(probabilities.sum(), 1.0)
    assert (probabilities >= 0).all()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Will clear Rs. 45,000 by next Friday.", IntentLabel.PROMISE_TO_PAY),
        ("Stop messaging me. Remove this number from your list.", IntentLabel.OPT_OUT),
        (
            "Please waive the late fee and we will settle next week.",
            IntentLabel.NEGOTIATION_REQUEST,
        ),
        ("The quantities billed do not match our goods receipt note.", IntentLabel.DISPUTE),
        ("This is already paid. Check your bank statement.", IntentLabel.ALREADY_PAID_CLAIM),
    ],
)
def test_classifier_handles_the_headline_intents(trained, text, expected):
    result = trained.predict_one(text)

    assert result.intent is expected, f"{text} -> {result.intent.value}"


def test_predict_one_reports_the_terms_that_drove_it(trained):
    result = trained.predict_one("Stop messaging me. Remove this number from your list.")

    assert result.top_terms
    terms = {term.term for term in result.top_terms}
    assert any(word in " ".join(terms) for word in ("stop", "remove", "number"))
    assert all(term.contribution > 0 for term in result.top_terms)
    assert "__" not in " ".join(terms), "feature-union prefixes should be stripped"


def test_explanation_names_the_class_and_the_evidence(trained):
    result = trained.predict_one("Please unsubscribe us from these reminders.")

    assert result.intent.value in result.explanation()


def test_top_terms_are_empty_rather_than_raising_for_an_unknown_class(trained):
    assert trained.top_terms("hello", "NOT_A_CLASS") == []


# --- quality gates ----------------------------------------------------------


def test_grouped_split_accuracy_beats_the_majority_baseline(test_predictions):
    y_true, y_pred, _ = test_predictions
    report = classification_report(y_true, y_pred)

    # Eight balanced classes, so chance is 0.125 and the majority baseline is
    # about the same. A useful model must clear both by a wide margin.
    assert report.accuracy > 0.55, report.accuracy
    assert report.macro_f1 > 0.55, report.macro_f1


def test_opt_out_recall_is_high_because_it_is_compliance_critical(test_predictions):
    y_true, y_pred, _ = test_predictions
    report = classification_report(y_true, y_pred)
    opt_out = next(row for row in report.per_class if row.label == "OPT_OUT")

    assert opt_out.recall >= 0.90, opt_out


def test_every_class_appears_in_the_report(test_predictions):
    y_true, y_pred, _ = test_predictions
    report = classification_report(y_true, y_pred)

    assert len(report.per_class) == 8
    assert all(row.support > 0 for row in report.per_class)


def test_a_random_split_scores_higher_than_a_grouped_one():
    """The point of grouping, demonstrated rather than asserted in prose."""

    scores = {}
    for strategy in ("grouped", "random"):
        corpus = build_corpus(size=1200, seed=42, split_strategy=strategy)
        x_train, y_train = corpus_texts_and_labels(corpus.for_split(SplitName.TRAIN))
        x_test, y_test = corpus_texts_and_labels(corpus.for_split(SplitName.TEST))
        model = ReplyIntentClassifier.fit(x_train, y_train, seed=42)
        scores[strategy] = classification_report(y_test, model.predict_labels(x_test)).macro_f1

    assert scores["random"] > scores["grouped"], scores


# --- evaluation utilities ---------------------------------------------------


def test_classification_report_rejects_mismatched_inputs():
    with pytest.raises(ValueError):
        classification_report(["A"], ["A", "B"])


def test_classification_report_rejects_an_empty_set():
    with pytest.raises(ValueError):
        classification_report([], [])


def test_confusion_matrix_rows_sum_to_support(test_predictions):
    y_true, y_pred, _ = test_predictions
    report = classification_report(y_true, y_pred)

    for index, row in enumerate(report.per_class):
        assert sum(report.confusion_matrix[index]) == row.support


def test_top_confusions_are_off_diagonal_and_ordered(test_predictions):
    y_true, y_pred, _ = test_predictions
    report = classification_report(y_true, y_pred)
    confusions = report.top_confusions()

    assert all(true != predicted for true, predicted, _ in confusions)
    counts = [count for _, _, count in confusions]
    assert counts == sorted(counts, reverse=True)


def test_calibration_report_buckets_cover_every_prediction(test_predictions):
    y_true, y_pred, confidences = test_predictions
    report = calibration_report(y_true, y_pred, confidences)

    assert sum(bucket.count for bucket in report.buckets) == len(y_true)
    assert 0.0 <= report.expected_calibration_error <= 1.0


def test_calibration_of_a_perfectly_calibrated_set_is_near_zero():
    y_true = ["A"] * 100
    y_pred = ["A"] * 90 + ["B"] * 10
    confidences = [0.9] * 100

    report = calibration_report(y_true, y_pred, confidences, n_buckets=5)

    assert report.expected_calibration_error < 0.01


def test_calibration_detects_an_overconfident_model():
    y_true = ["A"] * 100
    y_pred = ["A"] * 50 + ["B"] * 50
    confidences = [0.99] * 100

    report = calibration_report(y_true, y_pred, confidences, n_buckets=5)

    assert report.expected_calibration_error > 0.4


def test_calibration_rejects_mismatched_inputs():
    with pytest.raises(ValueError):
        calibration_report(["A"], ["A"], [0.5, 0.5])


def test_cascade_report_partitions_the_traffic(test_predictions):
    y_true, y_pred, confidences = test_predictions
    report = cascade_report(y_true, y_pred, confidences, threshold=0.6)

    assert report.resolved_by_model + report.escalated_to_llm == report.total
    assert np.isclose(report.resolution_rate + report.escalation_rate, 1.0)


def test_the_cascade_keeps_what_it_is_good_at(test_predictions):
    """The cascade only earns its place if kept traffic beats escalated traffic."""

    y_true, y_pred, confidences = test_predictions
    report = cascade_report(y_true, y_pred, confidences, threshold=0.6)

    assert report.escalated_to_llm > 0
    assert report.model_accuracy_on_resolved > report.model_accuracy_on_escalated


def test_a_threshold_of_zero_escalates_nothing(test_predictions):
    y_true, y_pred, confidences = test_predictions
    report = cascade_report(y_true, y_pred, confidences, threshold=0.0)

    assert report.escalated_to_llm == 0
    assert report.resolution_rate == 1.0


def test_a_higher_threshold_escalates_more(test_predictions):
    y_true, y_pred, confidences = test_predictions

    low = cascade_report(y_true, y_pred, confidences, threshold=0.5)
    high = cascade_report(y_true, y_pred, confidences, threshold=0.9)

    assert high.escalated_to_llm >= low.escalated_to_llm


def test_entity_report_is_exact_match_on_the_corpus(corpus):
    report = entity_report(corpus.examples)

    assert report.amount_expected > 100
    assert report.amount_accuracy == 1.0
    assert report.date_expected > 100
    assert report.date_accuracy == 1.0


def test_no_entity_false_positive_can_become_a_promise(corpus):
    """A date in an opt-out is found but gated away before it reaches a promise."""

    report = entity_report(corpus.examples)

    assert report.date_false_positives_actionable == 0
    assert report.amount_false_positives_actionable == 0


def test_report_formatters_render_every_class(test_predictions):
    y_true, y_pred, _ = test_predictions
    report = classification_report(y_true, y_pred)

    table = format_classification_report(report)
    matrix = format_confusion_matrix(report)

    for label in report.labels:
        assert label in table
    assert "accuracy" in table
    assert matrix.count("\n") == len(report.labels)


# --- persistence ------------------------------------------------------------


def test_classifier_round_trips_through_the_artifact_store(tmp_path, trained):
    settings = MLSettings(ml_artifacts_dir=tmp_path / "artifacts")

    path, metadata = save_classifier(
        trained,
        train_rows=823,
        metrics={"macro_f1": 0.73},
        notes="round-trip test",
        settings=settings,
    )
    loaded, loaded_metadata = load_classifier(settings=settings)

    assert path.exists()
    assert metadata.model_name == MODEL_NAME
    assert loaded_metadata.model_version == metadata.model_version
    assert loaded_metadata.train_rows == 823
    assert loaded.classes == trained.classes

    text = "Will clear Rs. 45,000 by next Friday."
    assert loaded.predict_one(text).intent is trained.predict_one(text).intent


def test_saved_metadata_records_the_feature_version(tmp_path, trained):
    settings = MLSettings(ml_artifacts_dir=tmp_path / "artifacts")

    _, metadata = save_classifier(trained, train_rows=10, settings=settings)

    assert metadata.feature_columns == [trained.feature_version]


def test_classifier_is_available_is_false_when_nothing_is_trained(tmp_path):
    settings = MLSettings(ml_artifacts_dir=tmp_path / "empty")

    assert classifier_is_available(settings=settings) is False


def test_classifier_is_available_is_true_after_saving(tmp_path, trained):
    settings = MLSettings(ml_artifacts_dir=tmp_path / "artifacts")
    save_classifier(trained, train_rows=10, settings=settings)

    assert classifier_is_available(settings=settings) is True
