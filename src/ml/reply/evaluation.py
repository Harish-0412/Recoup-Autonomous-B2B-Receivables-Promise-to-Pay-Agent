"""Evaluation for the reply-understanding feature.

Deliberately reports per-class numbers rather than one accuracy figure. An
aggregate hides exactly the classes that matter most here: ``OPT_OUT`` is the
compliance-critical one and also the rarest kind of message in a real inbox, so
it is the first thing a single accuracy number would bury.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

from src.ml.reply.dataset import LabelledReply
from src.ml.reply.entity_extraction import extract_amount, extract_date
from src.ml.schemas import IntentLabel

LABELS: list[str] = [label.value for label in IntentLabel]


class ClassMetrics(BaseModel):
    """Precision, recall and F1 for one intent class."""

    model_config = ConfigDict(frozen=True)

    label: str
    precision: float
    recall: float
    f1: float
    support: int


class ClassificationReport(BaseModel):
    """Per-class and aggregate classification quality."""

    model_config = ConfigDict(protected_namespaces=())

    n_examples: int
    accuracy: float
    macro_f1: float
    weighted_f1: float
    per_class: list[ClassMetrics]
    confusion_matrix: list[list[int]]
    labels: list[str] = Field(default_factory=lambda: list(LABELS))

    def top_confusions(self, limit: int = 5) -> list[tuple[str, str, int]]:
        """The most frequent (true, predicted) mistakes, biggest first."""

        pairs: list[tuple[str, str, int]] = []
        for i, true_label in enumerate(self.labels):
            for j, predicted_label in enumerate(self.labels):
                if i != j and self.confusion_matrix[i][j] > 0:
                    pairs.append((true_label, predicted_label, self.confusion_matrix[i][j]))
        pairs.sort(key=lambda item: item[2], reverse=True)
        return pairs[:limit]


class CalibrationBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    count: int
    mean_confidence: float
    observed_accuracy: float


class CalibrationReport(BaseModel):
    """Does a stated confidence mean what it says?"""

    model_config = ConfigDict(protected_namespaces=())

    buckets: list[CalibrationBucket]
    expected_calibration_error: float

    def summary(self) -> str:
        return (
            f"ECE={self.expected_calibration_error:.3f} over {len(self.buckets)} populated buckets"
        )


class EntityReport(BaseModel):
    """Exact-match accuracy for the deterministic extractors."""

    model_config = ConfigDict(protected_namespaces=())

    amount_expected: int
    amount_correct: int
    date_expected: int
    date_correct: int
    amount_false_positives: int
    date_false_positives: int

    #: False positives that survive the intent gate in ``service.py`` -- the
    #: only ones that could become a bogus promise record. A date found in an
    #: opt-out or a piece of small talk is discarded before it reaches a
    #: promise, so it is counted above but not here.
    amount_false_positives_actionable: int = 0
    date_false_positives_actionable: int = 0

    @property
    def amount_accuracy(self) -> float:
        return self.amount_correct / self.amount_expected if self.amount_expected else 1.0

    @property
    def date_accuracy(self) -> float:
        return self.date_correct / self.date_expected if self.date_expected else 1.0


class CascadeReport(BaseModel):
    """How much work Stage C absorbs before the LLM is consulted."""

    model_config = ConfigDict(protected_namespaces=())

    total: int
    resolved_by_model: int
    escalated_to_llm: int
    threshold: float
    model_accuracy_on_resolved: float
    model_accuracy_on_escalated: float
    #: Thresholds that actually applied, per predicted intent, when they differ
    #: from the base one.
    intent_thresholds: dict[str, float] = Field(default_factory=dict)
    #: Predictions Stage C acted on, broken down by the intent it predicted.
    kept_by_intent: dict[str, int] = Field(default_factory=dict)
    #: ...and how many of those were right. This is the number that matters for
    #: an intent whose false positives are expensive: overall precision counts
    #: predictions the cascade escalated and never acted on.
    kept_precision_by_intent: dict[str, float] = Field(default_factory=dict)
    escalated_by_intent: dict[str, int] = Field(default_factory=dict)

    @property
    def resolution_rate(self) -> float:
        return self.resolved_by_model / self.total if self.total else 0.0

    @property
    def escalation_rate(self) -> float:
        return self.escalated_to_llm / self.total if self.total else 0.0


def classification_report(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
) -> ClassificationReport:
    """Per-class precision/recall/F1 plus the confusion matrix."""

    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must be the same length")
    if not y_true:
        raise ValueError("cannot evaluate an empty set")

    label_list = list(labels or LABELS)
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=label_list, zero_division=0
    )
    macro_f1 = float(np.mean(f1)) if len(f1) else 0.0
    total_support = int(np.sum(support))
    weighted_f1 = float(np.sum(f1 * support) / total_support) if total_support else 0.0
    accuracy = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p) / len(y_true)

    return ClassificationReport(
        n_examples=len(y_true),
        accuracy=round(accuracy, 6),
        macro_f1=round(macro_f1, 6),
        weighted_f1=round(weighted_f1, 6),
        per_class=[
            ClassMetrics(
                label=label,
                precision=round(float(precision[i]), 6),
                recall=round(float(recall[i]), 6),
                f1=round(float(f1[i]), 6),
                support=int(support[i]),
            )
            for i, label in enumerate(label_list)
        ],
        confusion_matrix=confusion_matrix(y_true, y_pred, labels=label_list).tolist(),
        labels=label_list,
    )


def calibration_report(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    confidences: Sequence[float],
    *,
    n_buckets: int = 5,
) -> CalibrationReport:
    """Bucket predictions by confidence and compare stated to observed accuracy.

    The expected calibration error is the support-weighted mean gap between the
    two. A model claiming 0.9 that is right 0.6 of the time is worse than
    useless in a cascade, because the threshold stops meaning anything.
    """

    if not (len(y_true) == len(y_pred) == len(confidences)):
        raise ValueError("y_true, y_pred and confidences must be the same length")
    if n_buckets < 1:
        raise ValueError("n_buckets must be positive")

    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    buckets: list[CalibrationBucket] = []
    total = len(y_true)
    weighted_gap = 0.0

    for index in range(n_buckets):
        lower, upper = float(edges[index]), float(edges[index + 1])
        in_bucket = [
            position
            for position, confidence in enumerate(confidences)
            if (lower <= confidence < upper) or (index == n_buckets - 1 and confidence == 1.0)
        ]
        if not in_bucket:
            continue

        mean_confidence = float(np.mean([confidences[i] for i in in_bucket]))
        observed = sum(1 for i in in_bucket if y_true[i] == y_pred[i]) / len(in_bucket)
        weighted_gap += (len(in_bucket) / total) * abs(mean_confidence - observed)

        buckets.append(
            CalibrationBucket(
                lower=round(lower, 4),
                upper=round(upper, 4),
                count=len(in_bucket),
                mean_confidence=round(mean_confidence, 6),
                observed_accuracy=round(observed, 6),
            )
        )

    return CalibrationReport(
        buckets=buckets,
        expected_calibration_error=round(weighted_gap, 6),
    )


def entity_report(examples: Sequence[LabelledReply]) -> EntityReport:
    """Exact-match accuracy for amounts, and calendar-match for dates.

    A date counts only if it resolves to the right calendar day. "A date was
    found" is not the same thing and would flatter the extractor.
    """

    from src.ml.reply.service import ENTITY_BEARING_INTENTS

    amount_expected = amount_correct = 0
    date_expected = date_correct = 0
    amount_false_positives = date_false_positives = 0
    amount_false_positives_actionable = date_false_positives_actionable = 0

    for example in examples:
        actionable = example.intent in ENTITY_BEARING_INTENTS
        reference_dt = datetime.combine(example.reference_date, datetime.min.time())
        found_amount = extract_amount(example.text)
        found_date = extract_date(example.text, reference_dt)

        if example.expected_amount is not None:
            amount_expected += 1
            if found_amount == example.expected_amount:
                amount_correct += 1
        elif found_amount is not None:
            amount_false_positives += 1
            amount_false_positives_actionable += int(actionable)

        if example.expected_date is not None:
            date_expected += 1
            if found_date == example.expected_date:
                date_correct += 1
        elif found_date is not None:
            date_false_positives += 1
            date_false_positives_actionable += int(actionable)

    return EntityReport(
        amount_expected=amount_expected,
        amount_correct=amount_correct,
        date_expected=date_expected,
        date_correct=date_correct,
        amount_false_positives=amount_false_positives,
        date_false_positives=date_false_positives,
        amount_false_positives_actionable=amount_false_positives_actionable,
        date_false_positives_actionable=date_false_positives_actionable,
    )


def cascade_report(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    confidences: Sequence[float],
    *,
    threshold: float,
    intent_thresholds: Mapping[str, float] | None = None,
) -> CascadeReport:
    """How the confidence threshold splits traffic between Stage C and Stage A.

    The number worth reading is not the resolution rate on its own but the pair
    of accuracies: the cascade only earns its place if the model is markedly
    more accurate on what it keeps than on what it hands off.

    ``intent_thresholds`` mirrors the per-intent bars the cascade actually
    routes on, so the measurement matches production rather than a simplified
    version of it. ``kept_precision_by_intent`` is then the honest precision:
    it counts only the predictions the cascade acted on, which is what a false
    positive costs something for.
    """

    if not (len(y_true) == len(y_pred) == len(confidences)):
        raise ValueError("y_true, y_pred and confidences must be the same length")

    bars = dict(intent_thresholds or {})

    def bar_for(label: str) -> float:
        return max(threshold, bars.get(label, threshold))

    resolved = [i for i, c in enumerate(confidences) if c >= bar_for(y_pred[i])]
    escalated = [i for i, c in enumerate(confidences) if c < bar_for(y_pred[i])]

    def accuracy(indices: list[int]) -> float:
        if not indices:
            return 0.0
        return sum(1 for i in indices if y_true[i] == y_pred[i]) / len(indices)

    kept: dict[str, int] = {}
    correct: dict[str, int] = {}
    for i in resolved:
        label = y_pred[i]
        kept[label] = kept.get(label, 0) + 1
        if y_true[i] == label:
            correct[label] = correct.get(label, 0) + 1

    dropped: dict[str, int] = {}
    for i in escalated:
        label = y_pred[i]
        dropped[label] = dropped.get(label, 0) + 1

    return CascadeReport(
        total=len(y_true),
        resolved_by_model=len(resolved),
        escalated_to_llm=len(escalated),
        threshold=threshold,
        model_accuracy_on_resolved=round(accuracy(resolved), 6),
        model_accuracy_on_escalated=round(accuracy(escalated), 6),
        intent_thresholds={k: v for k, v in bars.items() if v > threshold},
        kept_by_intent=dict(sorted(kept.items())),
        kept_precision_by_intent={
            label: round(correct.get(label, 0) / count, 6) for label, count in sorted(kept.items())
        },
        escalated_by_intent=dict(sorted(dropped.items())),
    )


def format_confusion_matrix(report: ClassificationReport, *, width: int = 6) -> str:
    """Render the confusion matrix as fixed-width text for a terminal or report."""

    short = [label[:5] for label in report.labels]
    header = " " * 24 + "".join(f"{name:>{width}}" for name in short)
    lines = [header]
    for index, label in enumerate(report.labels):
        row = "".join(f"{count:>{width}}" for count in report.confusion_matrix[index])
        lines.append(f"{label:<24}{row}")
    return "\n".join(lines)


def format_classification_report(report: ClassificationReport) -> str:
    """Render per-class metrics as an aligned table."""

    lines = [
        f"{'class':<24}{'prec':>8}{'recall':>8}{'f1':>8}{'support':>9}",
        "-" * 57,
    ]
    for row in report.per_class:
        lines.append(
            f"{row.label:<24}{row.precision:>8.3f}{row.recall:>8.3f}"
            f"{row.f1:>8.3f}{row.support:>9d}"
        )
    lines.append("-" * 57)
    lines.append(f"{'accuracy':<24}{'':>8}{'':>8}{report.accuracy:>8.3f}{report.n_examples:>9d}")
    lines.append(f"{'macro f1':<24}{'':>8}{'':>8}{report.macro_f1:>8.3f}")
    lines.append(f"{'weighted f1':<24}{'':>8}{'':>8}{report.weighted_f1:>8.3f}")
    return "\n".join(lines)


class AggregatedClassMetric(BaseModel):
    """One class's metrics averaged over several independent splits."""

    model_config = ConfigDict(protected_namespaces=())

    label: str
    precision_mean: float
    precision_std: float
    precision_min: float
    precision_max: float
    recall_mean: float
    recall_std: float
    f1_mean: float
    f1_std: float
    support_total: int


class RepeatedEvaluation(BaseModel):
    """Metrics over repeated grouped splits, with their spread.

    A single grouped split puts only a handful of templates in the test set, so
    which ones land there moves per-class precision by tens of points. One
    split therefore cannot tell you whether a change helped; the spread across
    several can. Reporting the mean without the spread would be the same
    mistake in a more confident voice.
    """

    model_config = ConfigDict(protected_namespaces=())

    repeats: int
    seeds: list[int]
    accuracy_mean: float
    accuracy_std: float
    macro_f1_mean: float
    macro_f1_std: float
    per_class: list[AggregatedClassMetric]

    def for_label(self, label: str) -> AggregatedClassMetric | None:
        return next((row for row in self.per_class if row.label == label), None)


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    array = np.asarray(values, dtype=float)
    return float(array.mean()), float(array.std(ddof=0))


def aggregate_classification_reports(
    reports: Sequence[ClassificationReport],
    seeds: Sequence[int],
) -> RepeatedEvaluation:
    """Average per-class metrics across repeated splits and keep the spread."""

    if not reports:
        raise ValueError("need at least one report to aggregate")

    accuracy_mean, accuracy_std = _mean_std([r.accuracy for r in reports])
    macro_mean, macro_std = _mean_std([r.macro_f1 for r in reports])

    by_label: dict[str, list[ClassMetrics]] = {}
    for report in reports:
        for row in report.per_class:
            by_label.setdefault(row.label, []).append(row)

    per_class: list[AggregatedClassMetric] = []
    for label in sorted(by_label):
        rows = by_label[label]
        precisions = [row.precision for row in rows]
        p_mean, p_std = _mean_std(precisions)
        r_mean, r_std = _mean_std([row.recall for row in rows])
        f_mean, f_std = _mean_std([row.f1 for row in rows])
        per_class.append(
            AggregatedClassMetric(
                label=label,
                precision_mean=round(p_mean, 6),
                precision_std=round(p_std, 6),
                precision_min=round(min(precisions), 6),
                precision_max=round(max(precisions), 6),
                recall_mean=round(r_mean, 6),
                recall_std=round(r_std, 6),
                f1_mean=round(f_mean, 6),
                f1_std=round(f_std, 6),
                support_total=sum(row.support for row in rows),
            )
        )

    return RepeatedEvaluation(
        repeats=len(reports),
        seeds=list(seeds),
        accuracy_mean=round(accuracy_mean, 6),
        accuracy_std=round(accuracy_std, 6),
        macro_f1_mean=round(macro_mean, 6),
        macro_f1_std=round(macro_std, 6),
        per_class=per_class,
    )


def format_repeated_evaluation(evaluation: RepeatedEvaluation) -> str:
    """Per-class mean +/- std across repeated splits, with the observed range."""

    header = f"{'class':<24}{'prec':>16}{'range':>15}{'recall':>16}{'f1':>16}"
    lines = [
        f"over {evaluation.repeats} grouped splits (seeds {evaluation.seeds})",
        header,
        "-" * len(header),
    ]
    for row in evaluation.per_class:
        lines.append(
            f"{row.label:<24}"
            f"{row.precision_mean:>9.3f}+-{row.precision_std:<5.3f}"
            f"{row.precision_min:>7.2f}-{row.precision_max:<7.2f}"
            f"{row.recall_mean:>9.3f}+-{row.recall_std:<5.3f}"
            f"{row.f1_mean:>9.3f}+-{row.f1_std:<5.3f}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'accuracy':<24}{evaluation.accuracy_mean:>9.3f}+-{evaluation.accuracy_std:<5.3f}"
    )
    lines.append(
        f"{'macro f1':<24}{evaluation.macro_f1_mean:>9.3f}+-{evaluation.macro_f1_std:<5.3f}"
    )
    return "\n".join(lines)
