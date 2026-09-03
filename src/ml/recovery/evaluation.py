"""Evaluation for the recovery model, including the comparison that matters.

The headline number is not AUC. It is the head-to-head against the
rules-based scorer on the same held-out slice: does the trained model actually
rank invoices better than the hand-written formula it replaces? A model that
does not beat the rules it replaces is decoration, and reporting that honestly
is worth more than reporting an AUC with nothing to compare it to.

Calibration is treated as a first-class metric rather than a footnote, because
the probability is multiplied straight into a rupee amount by the
expected-value formula. A model that ranks perfectly but reads 0.9 where the
truth is 0.6 will systematically misprice every intervention decision.
"""

from collections.abc import Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    precision_recall_fscore_support,
    roc_auc_score,
)

from app.core.domain import CaseSnapshot


class CalibrationBin(BaseModel):
    model_config = ConfigDict(frozen=True)

    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_rate: float


class RecoveryMetrics(BaseModel):
    """Everything measured about one model on one split."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    split: str
    rows: int
    positive_rate: float
    threshold: float

    roc_auc: float
    average_precision: float
    precision: float
    recall: float
    f1: float
    accuracy: float

    brier_score: float
    log_loss: float
    expected_calibration_error: float
    calibration_bins: list[CalibrationBin] = Field(default_factory=list)

    def one_line(self) -> str:
        return (
            f"{self.model_name:<18} AUC={self.roc_auc:.3f}  AP={self.average_precision:.3f}  "
            f"F1={self.f1:.3f}  Brier={self.brier_score:.4f}  ECE={self.expected_calibration_error:.3f}"
        )


class RankingComparison(BaseModel):
    """Head-to-head ranking quality between two scorers on one split."""

    model_config = ConfigDict(protected_namespaces=())

    split: str
    rows: int
    challenger: str
    incumbent: str
    challenger_auc: float
    incumbent_auc: float
    challenger_brier: float
    incumbent_brier: float
    challenger_value_at_risk_at_k: float
    incumbent_value_at_risk_at_k: float
    k: int

    @property
    def auc_delta(self) -> float:
        return self.challenger_auc - self.incumbent_auc

    @property
    def challenger_wins(self) -> bool:
        return self.auc_delta > 0.0

    def verdict(self) -> str:
        direction = "beats" if self.challenger_wins else "does NOT beat"
        return (
            f"{self.challenger} {direction} {self.incumbent} on ranking: "
            f"AUC {self.challenger_auc:.3f} vs {self.incumbent_auc:.3f} "
            f"({self.auc_delta:+.3f}), Brier {self.challenger_brier:.4f} vs "
            f"{self.incumbent_brier:.4f}"
        )


def calibration_bins(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    *,
    n_bins: int = 10,
) -> tuple[list[CalibrationBin], float]:
    """Reliability curve plus the expected calibration error.

    ECE is the support-weighted mean gap between predicted probability and
    observed frequency: "when this model says 0.7, how often does it happen?"
    """

    y = np.asarray(y_true, dtype=float)
    p = np.asarray(probabilities, dtype=float)
    if y.shape != p.shape:
        raise ValueError("y_true and probabilities must be the same length")
    if y.size == 0:
        raise ValueError("cannot compute calibration on an empty set")

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins: list[CalibrationBin] = []
    error = 0.0

    for index in range(n_bins):
        lower, upper = float(edges[index]), float(edges[index + 1])
        if index == n_bins - 1:
            mask = (p >= lower) & (p <= upper)
        else:
            mask = (p >= lower) & (p < upper)
        count = int(mask.sum())
        if count == 0:
            continue

        mean_predicted = float(p[mask].mean())
        observed = float(y[mask].mean())
        error += (count / y.size) * abs(mean_predicted - observed)

        bins.append(
            CalibrationBin(
                lower=round(lower, 4),
                upper=round(upper, 4),
                count=count,
                mean_predicted=round(mean_predicted, 6),
                observed_rate=round(observed, 6),
            )
        )

    return bins, round(error, 6)


def evaluate(
    model_name: str,
    split_name: str,
    y_true: Sequence[int],
    probabilities: Sequence[float],
    *,
    threshold: float = 0.5,
    n_bins: int = 10,
) -> RecoveryMetrics:
    """Full metric set for one model on one split."""

    y = np.asarray(y_true, dtype=int)
    p = np.asarray(probabilities, dtype=float)
    if y.shape != p.shape:
        raise ValueError("y_true and probabilities must be the same length")
    if y.size == 0:
        raise ValueError("cannot evaluate an empty set")
    if not np.all((p >= 0.0) & (p <= 1.0)):
        raise ValueError("probabilities must lie in [0, 1]")

    predicted = (p >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y, predicted, average="binary", zero_division=0
    )

    # A split with one class present has no meaningful ranking metric; report
    # 0.5 (chance) rather than raising and losing the rest of the report.
    single_class = len(np.unique(y)) < 2
    bins, ece = calibration_bins(y, p, n_bins=n_bins)

    return RecoveryMetrics(
        model_name=model_name,
        split=split_name,
        rows=int(y.size),
        positive_rate=round(float(y.mean()), 6),
        threshold=threshold,
        roc_auc=0.5 if single_class else round(float(roc_auc_score(y, p)), 6),
        average_precision=(
            round(float(y.mean()), 6)
            if single_class
            else round(float(average_precision_score(y, p)), 6)
        ),
        precision=round(float(precision), 6),
        recall=round(float(recall), 6),
        f1=round(float(f1), 6),
        accuracy=round(float((predicted == y).mean()), 6),
        brier_score=round(float(brier_score_loss(y, p)), 6),
        log_loss=round(float(log_loss(y, p, labels=[0, 1])), 6),
        expected_calibration_error=ece,
        calibration_bins=bins,
    )


def value_at_risk_captured_at_k(
    y_true: Sequence[int],
    scores: Sequence[float],
    amounts: Sequence[float],
    *,
    k: int,
) -> float:
    """Rupees genuinely at risk among the k invoices worked first.

    Closer to the business question than AUC: a collections team works a ranked
    queue top-down and only gets through so many cases a day. The agent chases
    invoices *least* likely to self-cure, so the queue is ordered by ascending
    recovery probability, and the win is how much of the money that would
    otherwise have gone unrecovered lands in that top slice.

    Summing recovered rupees instead would reward the opposite behaviour --
    a scorer that queued the invoices about to be paid anyway would look best,
    which is exactly the false-intervention failure the EV formula exists to
    avoid. So the sum is over ``y == 0``: value the intervention could save.
    """

    y = np.asarray(y_true, dtype=float)
    s = np.asarray(scores, dtype=float)
    a = np.asarray(amounts, dtype=float)
    if not (y.shape == s.shape == a.shape):
        raise ValueError("y_true, scores and amounts must be the same length")
    if k <= 0:
        raise ValueError("k must be positive")

    k = min(k, y.size)
    order = np.argsort(s)[:k]
    return float(((1.0 - y[order]) * a[order]).sum())


def compare_rankings(
    split_name: str,
    y_true: Sequence[int],
    challenger_scores: Sequence[float],
    incumbent_scores: Sequence[float],
    amounts: Sequence[float],
    *,
    challenger: str,
    incumbent: str,
    k: int | None = None,
) -> RankingComparison:
    """Score the trained model against the rules-based scorer on one split."""

    y = np.asarray(y_true, dtype=int)
    if len(np.unique(y)) < 2:
        raise ValueError("cannot compare rankings on a single-class split")

    resolved_k = k or max(1, len(y) // 5)

    return RankingComparison(
        split=split_name,
        rows=int(y.size),
        challenger=challenger,
        incumbent=incumbent,
        challenger_auc=round(float(roc_auc_score(y, challenger_scores)), 6),
        incumbent_auc=round(float(roc_auc_score(y, incumbent_scores)), 6),
        challenger_brier=round(float(brier_score_loss(y, challenger_scores)), 6),
        incumbent_brier=round(float(brier_score_loss(y, incumbent_scores)), 6),
        challenger_value_at_risk_at_k=round(
            value_at_risk_captured_at_k(y, challenger_scores, amounts, k=resolved_k), 2
        ),
        incumbent_value_at_risk_at_k=round(
            value_at_risk_captured_at_k(y, incumbent_scores, amounts, k=resolved_k), 2
        ),
        k=resolved_k,
    )


def rules_based_scores(cases: Sequence[CaseSnapshot]) -> list[float]:
    """The incumbent's probabilities, obtained by actually running it.

    Imported lazily and called through its public entry point, so the
    comparison is against the scorer that ships rather than a reimplementation
    of it that might drift.
    """

    from app.core.scorer import estimate_recovery_probability

    return [float(estimate_recovery_probability(case)[0]) for case in cases]


def format_metrics_table(rows: Sequence[RecoveryMetrics]) -> str:
    """Aligned comparison table across models."""

    header = (
        f"{'model':<20}{'AUC':>8}{'AP':>8}{'prec':>8}{'recall':>8}"
        f"{'F1':>8}{'Brier':>9}{'ECE':>8}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.model_name:<20}{row.roc_auc:>8.3f}{row.average_precision:>8.3f}"
            f"{row.precision:>8.3f}{row.recall:>8.3f}{row.f1:>8.3f}"
            f"{row.brier_score:>9.4f}{row.expected_calibration_error:>8.3f}"
        )
    return "\n".join(lines)


def format_calibration_curve(metrics: RecoveryMetrics) -> str:
    """The reliability curve as text: predicted vs observed, bin by bin."""

    lines = [f"{'bin':<14}{'n':>6}{'predicted':>12}{'observed':>11}{'gap':>9}"]
    for row in metrics.calibration_bins:
        gap = row.mean_predicted - row.observed_rate
        lines.append(
            f"[{row.lower:.1f}, {row.upper:.1f})  {row.count:>6}{row.mean_predicted:>12.3f}"
            f"{row.observed_rate:>11.3f}{gap:>+9.3f}"
        )
    return "\n".join(lines)
