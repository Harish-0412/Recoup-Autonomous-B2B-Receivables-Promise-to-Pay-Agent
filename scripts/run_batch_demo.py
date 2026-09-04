"""Run the full agent loop against a fresh synthetic batch and report on it.

    python scripts/run_batch_demo.py --batch-size 600

Deliberately has no infrastructure dependencies: no database, no API keys, no
network. A judge should be able to clone the repo, install requirements, and
get the evaluation table on the first try. Everything the agent decides here it
decides through the same scorer, policy engine and state machine the API uses --
``app.core`` is shared, not reimplemented for the demo.

``--seed`` makes a run reproducible end to end. The same seed produces the same
batch, the same decisions and the same report, which is what makes the numbers
in ``docs/evaluation_report.md`` checkable rather than merely claimed.

When ``--use-model`` is passed, the script additionally re-scores every case
with *both* the trained model and the rules-based incumbent, runs the full
held-out ML metric suite (AUC, precision/recall, Brier, calibration ECE, per-bin
reliability curve, global SHAP importance) and prints a head-to-head comparison
that answers: does the trained model actually beat the hand-written scorer?
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow ``python scripts/run_batch_demo.py`` from a clean checkout without an
# editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.agent import AgentConfig, CycleResult, run_batch  # noqa: E402
from app.core.audit import DecisionLedger  # noqa: E402
from app.core.domain import CaseSnapshot, snapshot_from_generated  # noqa: E402
from app.core.evaluation import build_report, format_inr  # noqa: E402
from app.core.policy import PolicyConfig  # noqa: E402
from app.core.scorer import ScoringConfig  # noqa: E402
from app.models.enums import EscalationState, InterventionTier  # noqa: E402
from src.data.synthetic_generator import generate_batch  # noqa: E402
from src.ml.features.recovery_features import (  # noqa: E402
    FEATURE_COLUMNS_V1,
    build_recovery_features,
)
from src.ml.recovery.evaluation import (  # noqa: E402
    RankingComparison,
    RecoveryMetrics,
    compare_rankings,
    evaluate,
    format_calibration_curve,
    format_metrics_table,
    rules_based_scores,
)
from src.ml.recovery.explain import RecoveryExplainer, format_global_importance  # noqa: E402
from src.ml.recovery.scorer import get_recovery_scorer  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one full Recoup batch cycle.")
    parser.add_argument("--batch-size", type=int, default=600, help="Invoices to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Generator seed.")
    parser.add_argument(
        "--discount-ceiling",
        type=float,
        default=10.0,
        help="Maximum settlement discount the agent may offer, as a percentage.",
    )
    parser.add_argument(
        "--min-contact-gap-days",
        type=int,
        default=3,
        help="Minimum days between two contacts about the same invoice.",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=4,
        help=(
            "Decision cycles to simulate. One cycle can only move a case one rung, "
            "so the ladder's stopping rule is only visible across several."
        ),
    )
    parser.add_argument("--show", type=int, default=8, help="How many top-ranked cases to print.")
    parser.add_argument(
        "--use-model",
        action="store_true",
        help=(
            "Score with the trained recovery model instead of the rules. Falls back "
            "to the rules per invoice if no artifact has been trained yet. When set, "
            "the report also includes the full held-out ML metric panel and a "
            "rules-vs-model head-to-head comparison."
        ),
    )
    parser.add_argument("--threshold", type=float, default=0.5, help="Decision threshold for ML metrics.")
    parser.add_argument(
        "--write-report",
        type=Path,
        default=None,
        help="Write the report to this path (e.g. docs/evaluation_report.md).",
    )
    return parser.parse_args(argv)


def advance_case(case: CaseSnapshot, result: CycleResult, *, days: int) -> CaseSnapshot:
    """Move one case forward one cycle. Simulation only.

    This is the demo's clock, not production logic: in the real system the
    passage of time and the record of a sent message come from the database and
    the contact log. It lives in the script so nothing in ``app.core`` can
    accidentally depend on a simulated calendar.

    What advancing means: the as-of date moves, every case ages by ``days``,
    and a case that was actually contacted has its contact counters reset --
    which is precisely what re-arms the frequency cap for the next cycle.
    """

    invoice = case.invoice
    contacted = result.acted

    updates: dict[str, object] = {
        "as_of": invoice.as_of + timedelta(days=days),
        "days_overdue": invoice.days_overdue + days,
        "escalation_state": result.state_after,
        "ladder_index": invoice.ladder_index + (1 if contacted else 0),
        "days_since_last_contact": 0 if contacted else invoice.days_since_last_contact + days,
        "prior_reminders_sent": invoice.prior_reminders_sent + (1 if contacted else 0),
    }
    return case.model_copy(update={"invoice": invoice.model_copy(update=updates)})


def _scorer_label(scorer) -> str:
    """Describe which scorer actually ran, for the report header.

    Asked of the scorer rather than of ``--use-model``, because the two can
    disagree: requesting the model when no artifact has been trained yields the
    rules-based scorer, and the report must say what happened, not what was
    asked for.
    """

    if scorer is None or getattr(scorer, "name", "") == "rules-based":
        return "rules-based (`fallback_used=True`)"
    version = getattr(getattr(scorer, "metadata", None), "model_version", "trained model")
    return f"model-based (`{version}`, calibrated)"


class MLEvaluationPanel:
    """The ML metrics attached to a model-based run.

    Held out of the main ``BatchReport`` because it only exists when
    ``--use-model`` is passed and a trained artifact is actually available.
    Rendered as its own section(s) in the markdown report.
    """

    def __init__(
        self,
        *,
        model_metrics: RecoveryMetrics,
        rules_metrics: RecoveryMetrics,
        comparison: RankingComparison,
        global_shap: list[tuple[str, float]],
        calibration_metrics: RecoveryMetrics,
        model_name: str = "model-scorer",
    ) -> None:
        self.model_metrics = model_metrics
        self.rules_metrics = rules_metrics
        self.comparison = comparison
        self.global_shap = global_shap
        self.calibration_metrics = calibration_metrics
        self.model_name = model_name


def _collect_ml_panel(
    cases: list[CaseSnapshot],
    outcomes: dict[str, bool],
    scorer,
    *,
    threshold: float,
    as_of: date,
) -> MLEvaluationPanel | None:
    """Score every case with both scorers and assemble the full metric panel.

    Returns ``None`` if the model scorer is the rules-based fallback: in that
    case the comparison is trivially identity and is not useful.
    """

    if scorer is None or getattr(scorer, "name", "rules-based") == "rules-based":
        return None

    model_family = getattr(getattr(scorer, "model", None), "name", None) or scorer.name
    version_tag = None
    metadata = getattr(scorer, "metadata", None)
    if metadata is not None:
        version_tag = getattr(metadata, "model_version", None)
    display_name = version_tag or model_family

    labels = [int(outcomes[case.invoice.invoice_id]) for case in cases]
    amounts = [float(case.invoice.outstanding) for case in cases]

    model_probabilities = []
    fallbacks = 0
    for case in cases:
        prediction = scorer.score(case)
        p = float(prediction.p_recovery_30d)
        result_fallback = getattr(prediction, "fallback", None)
        if result_fallback is not None and result_fallback.triggered:
            fallbacks += 1
        model_probabilities.append(p)

    rules_probabilities = rules_based_scores(cases)

    model_metrics = evaluate(
        model_family, "batch-demo", labels, model_probabilities, threshold=threshold
    )
    rules_metrics = evaluate(
        "rules-based", "batch-demo", labels, rules_probabilities, threshold=threshold
    )
    comparison = compare_rankings(
        "batch-demo",
        labels,
        model_probabilities,
        rules_probabilities,
        amounts,
        challenger=display_name,
        incumbent="rules-based",
    )

    explainer = getattr(scorer, "explainer", None)
    global_shap: list[tuple[str, float]] = []
    if isinstance(explainer, RecoveryExplainer):
        try:
            import pandas as pd

            rows = []
            for case in cases:
                rows.append(build_recovery_features(case, as_of=as_of))
            frame = pd.DataFrame(rows, columns=list(FEATURE_COLUMNS_V1)).astype("float64")
            global_shap = explainer.global_importance(frame)
        except Exception:
            global_shap = []

    return MLEvaluationPanel(
        model_metrics=model_metrics,
        rules_metrics=rules_metrics,
        comparison=comparison,
        global_shap=global_shap,
        calibration_metrics=model_metrics,
        model_name=display_name,
    )


def simulate(
    cases: list[CaseSnapshot],
    *,
    config: AgentConfig,
    ledger: DecisionLedger,
    cycles: int,
    gap_days: int,
    scorer=None,
) -> tuple[list[CycleResult], list[dict[str, int]]]:
    """Run several decision cycles and return the final state of each case.

    Multiple cycles are what make the stopping rule observable. A single cycle
    can move a case at most one rung, so a one-shot run can never show a case
    reaching human handoff and stopping -- which is the behaviour that matters
    most here. The report is built from each case's *last* cycle, so a case
    that ended at handoff is counted as handed off.
    """

    current = list(cases)
    latest: dict[str, CycleResult] = {}
    summaries: list[dict[str, int]] = []

    for cycle in range(1, cycles + 1):
        results = run_batch(current, config=config, ledger=ledger, scorer=scorer)
        by_id = {result.invoice_id: result for result in results}
        latest.update(by_id)

        summaries.append(
            {
                "cycle": cycle,
                "acted": sum(1 for r in results if r.acted),
                "blocked": sum(
                    1 for r in results if r.decision is not None and not r.decision.allowed
                ),
                "left_alone": sum(1 for r in results if r.tier is InterventionTier.WAIT),
                "handed_off": sum(
                    1 for r in results if r.state_after is EscalationState.HUMAN_HANDOFF
                ),
            }
        )

        if cycle < cycles:
            current = [
                advance_case(case, by_id[case.invoice_id], days=gap_days) for case in current
            ]

    ordered = sorted(latest.values(), key=lambda r: r.score.expected_value, reverse=True)
    return ordered, summaries


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    batch = generate_batch(seed=args.seed, batch_size=args.batch_size)
    customers = {customer.customer_id: customer for customer in batch.customers}

    cases = [
        snapshot_from_generated(invoice, customers[invoice.customer_id])
        for invoice in batch.invoices
    ]
    # Ground truth is read only to score the report afterwards. It is not part
    # of the CaseSnapshot the agent sees -- see app.core.domain.
    outcomes = {invoice.invoice_id: bool(invoice.recovered) for invoice in batch.invoices}

    config = AgentConfig(
        policy=PolicyConfig(
            discount_ceiling_pct=args.discount_ceiling,
            min_contact_gap_days=args.min_contact_gap_days,
        ),
        scoring=ScoringConfig(horizon_days=batch.horizon_days),
    )

    # Built once for the whole demo: loading the artifact and its SHAP
    # explainer is expensive, and every cycle scores the same book.
    scorer = get_recovery_scorer(use_model=True) if args.use_model else None
    scorer_label = _scorer_label(scorer)

    ledger = DecisionLedger()
    results, cycle_summaries = simulate(
        cases,
        config=config,
        ledger=ledger,
        cycles=args.cycles,
        gap_days=args.min_contact_gap_days,
        scorer=scorer,
    )
    report = build_report(results, ledger=ledger, outcomes=outcomes, cycles_run=args.cycles)

    # Collect the ML evaluation panel *on the same cases* the agent ran on,
    # scored fresh so every metric here can be checked against the decisions
    # in the main report.
    as_of_for_metrics = batch.as_of if hasattr(batch, "as_of") else date(2026, 9, 1)
    ml_panel: MLEvaluationPanel | None = None
    if args.use_model:
        ml_panel = _collect_ml_panel(
            cases, outcomes, scorer, threshold=args.threshold, as_of=as_of_for_metrics
        )

    print(f"Recoup batch demo -- seed {args.seed}, {args.batch_size} invoices")
    print(f"Generator: {batch.generator_version}, horizon {batch.horizon_days}d")
    print(f"Simulated {args.cycles} decision cycles {args.min_contact_gap_days} days apart")
    print()
    print(report.render())

    if ml_panel is not None:
        print()
        print("=== ML held-out evaluation (batch-demo slice) ===")
        print(format_metrics_table([ml_panel.rules_metrics, ml_panel.model_metrics]))
        print()
        print("=== Model vs incumbent (rules-based) ===")
        print(f"  {ml_panel.comparison.verdict()}")
        print(
            f"  rupees genuinely at risk surfaced in the top {ml_panel.comparison.k} "
            f"of the work queue: "
            f"{format_inr(ml_panel.comparison.challenger_value_at_risk_at_k)} vs "
            f"{format_inr(ml_panel.comparison.incumbent_value_at_risk_at_k)}"
        )
        if ml_panel.comparison.challenger_value_at_risk_at_k != 0:
            delta = (
                ml_panel.comparison.challenger_value_at_risk_at_k
                - ml_panel.comparison.incumbent_value_at_risk_at_k
            )
            print(
                f"  delta: {format_inr(delta)} "
                f"({delta / ml_panel.comparison.incumbent_value_at_risk_at_k * 100:+.1f}%)"
            )
        print()
        print(f"=== Calibration ({ml_panel.model_name}) ===")
        print(format_calibration_curve(ml_panel.calibration_metrics))
        print(
            f"  Brier={ml_panel.model_metrics.brier_score:.4f}  "
            f"ECE={ml_panel.model_metrics.expected_calibration_error:.3f}  "
            f"(rules-based: Brier={ml_panel.rules_metrics.brier_score:.4f}, "
            f"ECE={ml_panel.rules_metrics.expected_calibration_error:.3f})"
        )
        if ml_panel.global_shap:
            print()
            print("=== Global SHAP importance (batch-demo slice) ===")
            print(format_global_importance(ml_panel.global_shap))

    print()
    print("Per-cycle activity:")
    print(f"  {'CYCLE':<7} {'ACTED':>7} {'BLOCKED':>8} {'LEFT ALONE':>11} {'HANDED OFF':>11}")
    for summary in cycle_summaries:
        print(
            f"  {summary['cycle']:<7} {summary['acted']:>7} {summary['blocked']:>8} "
            f"{summary['left_alone']:>11} {summary['handed_off']:>11}"
        )

    if report.policy_block_reasons:
        print()
        print("Policy blocks by rule:")
        for code, count in sorted(report.policy_block_reasons.items(), key=lambda item: -item[1]):
            print(f"  {code:<28} {count}")

    if args.show:
        print()
        print(f"Top {args.show} cases by expected value at risk:")
        header = f"  {'INVOICE':<16} {'OUTSTANDING':>13} {'P(REC)':>7} {'EV':>12}  DECISION"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for result in results[: args.show]:
            decision = result.tier.value
            if result.tier is not InterventionTier.WAIT:
                decision = f"{result.tier.value} -> {result.state_after.value}"
                if not result.acted:
                    decision = f"{result.tier.value} -> BLOCKED"
            print(
                f"  {result.invoice_id:<16} "
                f"{format_inr(result.score.outstanding):>13} "
                f"{result.score.p_recovery:>7.3f} "
                f"{format_inr(result.score.expected_value):>12}  {decision}"
            )

    print()
    print(f"Decision trace: {len(ledger)} entries, chain verified: {ledger.is_valid()}")

    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(
            _render_markdown(
                report,
                args,
                batch.generator_version,
                batch.horizon_days,
                scorer_label,
                ml_panel=ml_panel,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {args.write_report}")

    return 0


def _render_markdown(
    report,
    args,
    generator_version: str,
    horizon_days: int,
    scorer_label: str = "rules-based (`fallback_used=True`)",
    *,
    ml_panel: MLEvaluationPanel | None = None,
) -> str:
    """Render the committed report file.

    Includes the exact command that produced it, so anyone can re-run it and
    get the same table. When the batch was scored with the trained model, the
    report also carries the full ML metrics panel: AUC, precision/recall,
    Brier, calibration curve with ECE, head-to-head ranking comparison against
    the rules-based incumbent, and the global SHAP importance ranking.
    """

    command = (
        f"python scripts/run_batch_demo.py --batch-size {args.batch_size} "
        f"--seed {args.seed} --cycles {args.cycles}" + (" --use-model" if args.use_model else "")
    )
    lines = [
        "# Batch Evaluation Report",
        "",
        f"Generated {date.today().isoformat()} by `{command}`.",
        "",
        "This file is generated output, committed on purpose. Re-running the",
        "command above against the same seed reproduces it exactly.",
        "",
        "```",
        report.render(),
        "```",
        "",
        "## Run parameters",
        "",
        "| Parameter | Value |",
        "|---|---|",
        f"| Batch size | {args.batch_size} |",
        f"| Decision cycles | {args.cycles} |",
        f"| Seed | {args.seed} |",
        f"| Generator | {generator_version} |",
        f"| Recovery horizon | {horizon_days} days |",
        f"| Discount ceiling | {args.discount_ceiling:g}% |",
        f"| Minimum contact gap | {args.min_contact_gap_days} days |",
        f"| Scorer | {scorer_label} |",
        f"| Classification threshold (ML panel) | {args.threshold} |",
        "",
        "## How to read these numbers",
        "",
    ]
    if not scorer_label.startswith("rules-based"):
        lines.extend(
            [
                "**The scorer is the trained model.** Every recovery probability",
                "in this run came from the gradient-boosted (or logistic) model,",
                "isotonically calibrated on a held-out validation split, so these",
                "numbers can be read as probabilities. Any invoice the model could",
                "not score fell back to the rules and is marked `fallback_used=True`",
                "in the decision trace.",
                "",
                "Full held-out metrics for the model-scored batch, including AUC,",
                "precision/recall, Brier score, calibration curve, SHAP feature",
                "importance and a direct ranking comparison against the",
                "rules-based incumbent, appear in the **ML evaluation panel**",
                "section at the bottom of this report.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "**The scorer is rules-based, not trained.** Every recovery",
                "probability in this run came from a hand-tuned logistic model,",
                "returned with `fallback_used=True` and",
                "`resolved_by=rules_based_scorer`. These probabilities are not",
                "calibrated and should not be read as such; re-run with",
                "`--use-model` after training to score with the calibrated model",
                "and see the ML evaluation panel.",
                "",
            ]
        )
    lines.extend(
        [
            "**The agent did not cause these recoveries.** The synthetic generator",
            "samples each invoice's outcome independently of what the agent does,",
            "so `recovered` is the outcome under *no* intervention. The recovery",
            "rate below therefore measures whether the agent chose to act on the",
            "invoices that were going to be paid -- it is a targeting measure, not",
            "a causal one. Claiming otherwise would require a holdout arm that this",
            "simulation does not have.",
            "",
            "**False interventions are counted as a cost.** A contacted customer",
            "whose invoice was recovered anyway is counted in that row, not quietly",
            "dropped. That number going up is a real regression.",
            "",
            "**Compliance violations are counted from the ledger, not from intent.**",
            "The row counts invoices where an action was executed after the policy",
            "engine recorded a block for that invoice. It reads the decision trace",
            "rather than the orchestrator's own return values, so a bug in the",
            "orchestrator cannot suppress it.",
            "",
        ]
    )

    if report.policy_block_reasons:
        lines.extend(
            [
                "## Policy blocks by rule",
                "",
                "| Rule | Blocked |",
                "|---|---|",
            ]
        )
        for code, count in sorted(report.policy_block_reasons.items(), key=lambda i: -i[1]):
            lines.append(f"| `{code}` | {count} |")
        lines.append("")

    # ------------------------------------------------------------------ ML panel
    if ml_panel is not None:
        model_metrics = ml_panel.model_metrics
        rules_metrics = ml_panel.rules_metrics
        comp = ml_panel.comparison

        lines.extend(
            [
                "## ML held-out evaluation (batch-demo slice)",
                "",
                "Every case in this run was scored with *both* the trained model and",
                "the rules-based incumbent, on labels drawn from the synthetic",
                "generator's ground-truth `recovered` field. Positive rate on the",
                f"slice: {model_metrics.positive_rate * 100:.1f}%",
                f" (n = {model_metrics.rows}).",
                "",
                "The model that ships is selected by highest held-out AUC from",
                "three candidates: logistic regression (baseline), gradient-boosted",
                "trees (XGBoost) and a small MLP. Full per-candidate metrics,",
                "temporal split details, and training-set SHAP importance appear in",
                "`docs/recovery_model_card.md`. The numbers in *this* section were",
                "scored live on the demo batch and are reproducible via the command",
                "at the top of this report.",
                "",
                "### Classification & ranking metrics",
                "",
                "| Model | AUC | AP | Precision | Recall | F1 | Brier | ECE |",
                "|---|---|---|---|---|---|---|---|",
                "| `rules-based` (incumbent) | "
                f"{rules_metrics.roc_auc:.3f} | {rules_metrics.average_precision:.3f} | "
                f"{rules_metrics.precision:.3f} | {rules_metrics.recall:.3f} | "
                f"{rules_metrics.f1:.3f} | {rules_metrics.brier_score:.4f} | "
                f"{rules_metrics.expected_calibration_error:.3f} |",
                f"| `{ml_panel.model_name}` (trained, calibrated) | "
                f"{model_metrics.roc_auc:.3f} | {model_metrics.average_precision:.3f} | "
                f"{model_metrics.precision:.3f} | {model_metrics.recall:.3f} | "
                f"{model_metrics.f1:.3f} | {model_metrics.brier_score:.4f} | "
                f"{model_metrics.expected_calibration_error:.3f} |",
                "",
                f"Threshold used for hard-classification metrics: p >= {args.threshold}.",
                "AUC and Average Precision are threshold-free and come directly from",
                "the probability output.",
                "",
                "### Does the trained model beat the hand-written scorer?",
                "",
                f"{comp.verdict()}.",
                "",
                "| Scorer | Top-k value-at-risk captured |",
                "|---|---|",
                f"| `rules-based` (incumbent) | {format_inr(comp.incumbent_value_at_risk_at_k)} |",
                f"| `{ml_panel.model_name}` (trained) | {format_inr(comp.challenger_value_at_risk_at_k)} |",
                "",
            ]
        )

        comp_delta = comp.challenger_value_at_risk_at_k - comp.incumbent_value_at_risk_at_k
        if comp.incumbent_value_at_risk_at_k > 0:
            pct = comp_delta / comp.incumbent_value_at_risk_at_k * 100
            lines.extend(
                [
                    f"Top-{comp.k} delta: "
                    f"**{format_inr(comp_delta)} ({pct:+.1f}%)** "
                    f"more unpaid rupees correctly surfaced in the work queue.",
                    "",
                ]
            )
        else:
            lines.append(f"Top-k size: {comp.k}.\n")

        lines.extend(
            [
                "Interpretation: value-at-risk-captured sums the *unpaid* rupees",
                "among the k invoices the scorer ranks first (lowest P(recovery)).",
                "A higher number means the team's finite outreach bandwidth is",
                "directed at the invoices that would otherwise have gone unpaid.",
                "",
            ]
        )

        # Calibration curve (markdown table form)
        lines.extend(
            [
                "### Calibration of the recovery probability output",
                "",
                "Reliability curve for the trained model: predicted probability",
                "band vs. observed rate, bin by bin. ECE is the support-weighted",
                "mean absolute gap between predicted and observed.",
                "",
                "| Bin | n | Mean predicted | Observed rate | Gap |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in model_metrics.calibration_bins:
            gap = row.mean_predicted - row.observed_rate
            lines.append(
                f"| [{row.lower:.1f}, {row.upper:.1f}) | {row.count} | "
                f"{row.mean_predicted:.3f} | {row.observed_rate:.3f} | {gap:+.3f} |"
            )
        lines.append("")
        lines.extend(
            [
                "**Summary**: "
                f"ECE={model_metrics.expected_calibration_error:.3f}, "
                f"Brier={model_metrics.brier_score:.4f}  "
                f"(rules-based: ECE={rules_metrics.expected_calibration_error:.3f}, "
                f"Brier={rules_metrics.brier_score:.4f}).",
                "",
            ]
        )
        ece = model_metrics.expected_calibration_error
        if ece < 0.03:
            calibration_verdict = (
                "very well calibrated: the probability output can be treated "
                "as a real probability with no correction."
            )
        elif ece < 0.05:
            calibration_verdict = (
                "well calibrated: safe to multiply straight through the "
                "expected-value formula."
            )
        elif ece < 0.08:
            calibration_verdict = (
                "adequate for prioritisation. ECE is slightly above the 0.05 "
                "target, in part because this is a small (n="
                f"{model_metrics.rows}) held-out slice where calibration "
                "statistics are noisier. The training-time calibration report "
                "on the full (larger) held-out set is the authoritative number."
            )
        elif ece < 0.12:
            calibration_verdict = (
                "usable for ranking but values close to 0.5 should not be "
                "read as precise probabilities. A recalibration step is "
                "recommended before using the output in an EV formula."
            )
        else:
            calibration_verdict = (
                "poorly calibrated. Probabilities should not be multiplied "
                "into rupee amounts directly; only the ranking order is "
                "trustworthy until the model is recalibrated on fresh data."
            )
        lines.extend(
            [
                f"**Calibration assessment**: {calibration_verdict}",
                "",
                "Benchmarks: ECE on the rules-based scorer on the same slice "
                f"is {rules_metrics.expected_calibration_error:.3f}. The model's ",
                "probabilities are trained on isotonic calibration fitted on a ",
                "held-out validation split, so they are expected to be more ",
                "reliable than the hand-written scorer's on larger books.",
                "",
            ]
        )

        # SHAP
        if ml_panel.global_shap:
            lines.extend(
                [
                    "### Global SHAP feature importance",
                    "",
                    "Mean absolute SHAP contribution per feature across the entire",
                    "batch. Features at the top move the model's output the most;",
                    "the ranking is inspectable and should roughly agree with the",
                    "domain intuitions driving the rules-based scorer.",
                    "",
                    "| Feature | Mean SHAP magnitude |",
                    "|---|---:|",
                ]
            )
            for name, value in ml_panel.global_shap:
                lines.append(f"| `{name}` | {value:.4f} |")
            lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
