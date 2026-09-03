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


def simulate(
    cases: list[CaseSnapshot],
    *,
    config: AgentConfig,
    ledger: DecisionLedger,
    cycles: int,
    gap_days: int,
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
        results = run_batch(current, config=config, ledger=ledger)
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

    ledger = DecisionLedger()
    results, cycle_summaries = simulate(
        cases,
        config=config,
        ledger=ledger,
        cycles=args.cycles,
        gap_days=args.min_contact_gap_days,
    )
    report = build_report(results, ledger=ledger, outcomes=outcomes, cycles_run=args.cycles)

    print(f"Recoup batch demo -- seed {args.seed}, {args.batch_size} invoices")
    print(f"Generator: {batch.generator_version}, horizon {batch.horizon_days}d")
    print(f"Simulated {args.cycles} decision cycles {args.min_contact_gap_days} days apart")
    print()
    print(report.render())

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
            _render_markdown(report, args, batch.generator_version, batch.horizon_days),
            encoding="utf-8",
        )
        print(f"Wrote {args.write_report}")

    return 0


def _render_markdown(report, args, generator_version: str, horizon_days: int) -> str:
    """Render the committed report file.

    Includes the exact command that produced it, so anyone can re-run it and
    get the same table.
    """

    command = (
        f"python scripts/run_batch_demo.py --batch-size {args.batch_size} "
        f"--seed {args.seed} --cycles {args.cycles}"
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
        "| Scorer | rules-based (`fallback_used=True`) |",
        "",
        "## How to read these numbers",
        "",
        "**The scorer is rules-based, not trained.** Every recovery probability",
        "in this run came from a hand-tuned logistic model, returned with",
        "`fallback_used=True` and `resolved_by=rules_based_scorer`. Phase 4",
        "replaces it with a trained, calibrated model; until then these",
        "probabilities are not calibrated and should not be read as such.",
        "",
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

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
