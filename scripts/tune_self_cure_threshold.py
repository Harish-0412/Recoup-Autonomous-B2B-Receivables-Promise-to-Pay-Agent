"""Sweep the self-cure threshold and pick an operating point on evidence.

    python scripts/tune_self_cure_threshold.py --use-model

``ScoringConfig.self_cure_probability`` decides when an invoice is left alone
because it looks like it will be paid without chasing. It was set to 0.75
against the rules-based scorer, whose probabilities carry an expected
calibration error of 0.167 -- so "0.75" did not mean a 75% chance of anything.
The trained model reaches ECE 0.031, which makes the same number mean something
different, and therefore worth re-deriving rather than inheriting.

What the sweep measures, per threshold:

``correctly_left_alone``
    WAIT, and the invoice was recovered anyway. The saving.
``missed_recoveries``
    WAIT, and it was not recovered. The cost of restraint.
``false_interventions``
    Contacted, and it would have been recovered anyway. The cost of chasing.

**What this cannot measure.** The generator samples outcomes independently of
what the agent does, so chasing an invoice does not change whether it is paid.
No causal uplift can be computed here, and none is claimed. What *is* measurable
is targeting: given a fixed book, which invoices does each threshold choose to
leave alone, and how often is that choice right. The operating point is
therefore chosen on the precision of the WAIT decision, not on a rupee figure
that would require an uplift assumption to invent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.agent import AgentConfig, run_batch  # noqa: E402
from app.core.domain import snapshot_from_generated  # noqa: E402
from app.core.policy import PolicyConfig  # noqa: E402
from app.core.scorer import DEFAULT_INTERVENTION_COST, ScoringConfig  # noqa: E402
from app.models.enums import InterventionTier  # noqa: E402
from src.data.synthetic_generator import generate_batch  # noqa: E402
from src.ml.recovery.scorer import get_recovery_scorer  # noqa: E402

DEFAULT_GRID = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00]


def evaluate_threshold(cases, outcomes, *, threshold, horizon_days, scorer, policy):
    """One pass over the book at one threshold. Single cycle, so the WAIT
    decision is measured directly rather than through four rounds of contact
    caps that would confound it with the rate limiter."""

    config = AgentConfig(
        policy=policy,
        scoring=ScoringConfig(horizon_days=horizon_days, self_cure_probability=threshold),
    )
    results = run_batch(cases, config=config, scorer=scorer)

    waited = acted = 0
    correctly_left_alone = missed = false_interventions = 0
    value_left_alone = value_chased = 0.0
    # Outstanding on invoices that went unpaid, split by what the agent did.
    # These are the only rupees a contact could ever have saved.
    at_risk_chased = at_risk_missed = 0.0

    for result in results:
        recovered = outcomes.get(result.invoice_id, False)
        outstanding = result.score.outstanding
        if result.tier is InterventionTier.WAIT:
            waited += 1
            value_left_alone += outstanding
            if recovered:
                correctly_left_alone += 1
            else:
                missed += 1
                at_risk_missed += outstanding
        else:
            acted += 1
            value_chased += outstanding
            if recovered:
                false_interventions += 1
            else:
                at_risk_chased += outstanding

    total = len(results)
    return {
        "threshold": threshold,
        "total": total,
        "waited": waited,
        "acted": acted,
        "correctly_left_alone": correctly_left_alone,
        "missed_recoveries": missed,
        "false_interventions": false_interventions,
        # Of the invoices we chose not to chase, how often was that right?
        "wait_precision": round(correctly_left_alone / waited, 4) if waited else 0.0,
        # Of the invoices we chased, how many did not need it?
        "wasted_contact_rate": round(false_interventions / acted, 4) if acted else 0.0,
        "value_left_alone": round(value_left_alone, 2),
        "value_chased": round(value_chased, 2),
        "at_risk_chased": round(at_risk_chased, 2),
        "at_risk_missed": round(at_risk_missed, 2),
    }


def net_value(row: dict, *, uplift: float, intervention_cost: float) -> float:
    """Expected rupees this threshold is worth, under a stated uplift.

    ``uplift`` is the one thing the simulation cannot supply: the fraction of
    non-paying invoices that a reminder actually converts. The generator samples
    outcomes independently of what the agent does, so chasing changes nothing
    inside it, and any uplift figure derived from this data would be invented.

    So it is an input, not a result. The value of chasing is the at-risk money
    among the invoices contacted, times the share a contact converts; the cost
    is one intervention fee per contact. Every number below is conditional on
    the uplift you assume, which is why the sweep reports a break-even rather
    than a single answer.
    """

    return uplift * row["at_risk_chased"] - intervention_cost * row["acted"]


def break_even_uplift(lower: dict, higher: dict, *, intervention_cost: float) -> float | None:
    """The uplift at which moving from ``lower`` to ``higher`` starts paying.

    Raising the threshold chases more invoices: it costs extra contacts and
    recovers at-risk money that would otherwise have been left alone. Below the
    returned uplift the extra contacts are not worth it; above it they are.
    """

    extra_contacts = higher["acted"] - lower["acted"]
    extra_at_risk = higher["at_risk_chased"] - lower["at_risk_chased"]
    if extra_contacts <= 0 or extra_at_risk <= 0:
        return None
    return (intervention_cost * extra_contacts) / extra_at_risk


def choose_operating_point(
    rows: list[dict],
    *,
    uplift: float,
    intervention_cost: float,
    min_wait_precision: float,
) -> dict | None:
    """Highest net value under the stated uplift, subject to a floor on silence.

    Two conditions, because either alone gives a bad answer. Net value on its
    own would chase almost everything -- the at-risk invoices are worth lakhs
    and a contact costs a few hundred rupees, so the arithmetic always says
    send one more. The precision floor is the guard-rail that keeps the agent
    from becoming an indiscriminate mailer: when it does decide to stay silent,
    it has to be right at least ``min_wait_precision`` of the time, or it is
    not making a judgement at all.
    """

    eligible = [
        row for row in rows if row["waited"] and row["wait_precision"] >= min_wait_precision
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: net_value(row, uplift=uplift, intervention_cost=intervention_cost),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=600)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--seeds",
        type=int,
        default=5,
        help="books to average over; one book is a sample, not a curve",
    )
    parser.add_argument("--use-model", action="store_true", help="score with the trained model")
    parser.add_argument(
        "--uplift",
        type=float,
        default=0.10,
        help=(
            "assumed fraction of non-paying invoices a reminder converts. Not "
            "measurable in the simulation; the sweep reports the break-even too"
        ),
    )
    parser.add_argument(
        "--intervention-cost",
        type=float,
        default=DEFAULT_INTERVENTION_COST,
        help="rupee cost of one contact (default: the scorer's configured cost)",
    )
    parser.add_argument(
        "--min-wait-precision",
        type=float,
        default=0.80,
        help="when the agent stays silent it must be right at least this often",
    )
    parser.add_argument("--out", type=Path, default=Path("data/threshold_sweep"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    scorer = get_recovery_scorer(use_model=True) if args.use_model else None
    label = "trained model" if args.use_model else "rules-based scorer"
    policy = PolicyConfig()

    seeds = [args.seed + offset for offset in range(args.seeds)]
    per_seed: dict[int, list[dict]] = {}
    for seed in seeds:
        batch = generate_batch(seed=seed, batch_size=args.batch_size)
        customers = {c.customer_id: c for c in batch.customers}
        cases = [snapshot_from_generated(inv, customers[inv.customer_id]) for inv in batch.invoices]
        outcomes = {inv.invoice_id: bool(inv.recovered) for inv in batch.invoices}
        per_seed[seed] = [
            evaluate_threshold(
                cases,
                outcomes,
                threshold=t,
                horizon_days=batch.horizon_days,
                scorer=scorer,
                policy=policy,
            )
            for t in DEFAULT_GRID
        ]

    # Pool across books rather than averaging rates, so a seed with few WAITs
    # cannot swing a precision figure it barely contributed to.
    pooled = []
    for index, threshold in enumerate(DEFAULT_GRID):
        rows = [per_seed[s][index] for s in seeds]
        waited = sum(r["waited"] for r in rows)
        acted = sum(r["acted"] for r in rows)
        cla = sum(r["correctly_left_alone"] for r in rows)
        missed = sum(r["missed_recoveries"] for r in rows)
        false_i = sum(r["false_interventions"] for r in rows)
        pooled.append(
            {
                "threshold": threshold,
                "total": sum(r["total"] for r in rows),
                "waited": waited,
                "acted": acted,
                "correctly_left_alone": cla,
                "missed_recoveries": missed,
                "false_interventions": false_i,
                "wait_precision": round(cla / waited, 4) if waited else 0.0,
                "wasted_contact_rate": round(false_i / acted, 4) if acted else 0.0,
                "value_left_alone": round(sum(r["value_left_alone"] for r in rows), 2),
                "value_chased": round(sum(r["value_chased"] for r in rows), 2),
                "at_risk_chased": round(sum(r["at_risk_chased"] for r in rows), 2),
                "at_risk_missed": round(sum(r["at_risk_missed"] for r in rows), 2),
            }
        )

    print(f"self-cure threshold sweep -- {label}, {len(seeds)} books of {args.batch_size}")
    print(f"seeds {seeds}, {pooled[0]['total']} invoice-decisions per threshold")
    print()
    header = (
        f"{'thresh':>7}{'waited':>8}{'acted':>7}{'left alone ok':>15}"
        f"{'missed':>8}{'false intv':>12}{'WAIT prec':>11}{'wasted%':>9}"
    )
    print(header)
    print("-" * len(header))
    for row in pooled:
        print(
            f"{row['threshold']:>7.2f}{row['waited']:>8}{row['acted']:>7}"
            f"{row['correctly_left_alone']:>15}{row['missed_recoveries']:>8}"
            f"{row['false_interventions']:>12}{row['wait_precision']:>11.3f}"
            f"{row['wasted_contact_rate']:>9.1%}"
        )

    print()
    print("  marginal trade, moving one step up the grid (chasing more):")
    print(f"    {'step':<16}{'extra contacts':>16}{'at-risk freed':>16}{'break-even uplift':>20}")
    for lower, higher in zip(pooled, pooled[1:], strict=False):
        be = break_even_uplift(lower, higher, intervention_cost=args.intervention_cost)
        step = f"{lower['threshold']:.2f} -> {higher['threshold']:.2f}"
        if be is None:
            print(f"    {step:<16}{'-':>16}{'-':>16}{'no gain':>20}")
        else:
            print(
                f"    {step:<16}{higher['acted'] - lower['acted']:>16}"
                f"{(higher['at_risk_chased'] - lower['at_risk_chased']) / 1e5:>15.1f}L"
                f"{be:>19.2%}"
            )
    print(
        "    Read: chasing the extra invoices in a step pays off only if a reminder"
        " converts more than the break-even share of non-payers."
    )

    chosen = choose_operating_point(
        pooled,
        uplift=args.uplift,
        intervention_cost=args.intervention_cost,
        min_wait_precision=args.min_wait_precision,
    )
    print()
    print(
        f"  Sensitivity -- best threshold at each assumed uplift "
        f"(WAIT precision must stay >= {args.min_wait_precision:.2f}):"
    )
    sensitivity = {}
    for candidate_uplift in (0.02, 0.05, 0.10, 0.20, 0.35, 0.50):
        pick = choose_operating_point(
            pooled,
            uplift=candidate_uplift,
            intervention_cost=args.intervention_cost,
            min_wait_precision=args.min_wait_precision,
        )
        sensitivity[f"{candidate_uplift:.2f}"] = pick["threshold"] if pick else None
        label_pick = f"{pick['threshold']:.2f}" if pick else "none"
        print(f"    uplift {candidate_uplift:>5.0%}  ->  self_cure_probability = {label_pick}")

    report = {
        "scorer": label,
        "seeds": seeds,
        "batch_size": args.batch_size,
        "min_wait_precision": args.min_wait_precision,
        "uplift": args.uplift,
        "intervention_cost": args.intervention_cost,
        "sensitivity": sensitivity,
        "grid": DEFAULT_GRID,
        "pooled": pooled,
        "chosen": chosen,
    }
    path = args.out / f"self_cure_sweep_{'model' if args.use_model else 'rules'}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
