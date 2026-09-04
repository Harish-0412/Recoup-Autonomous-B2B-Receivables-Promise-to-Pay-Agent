"""Train, evaluate and save the contact-timing bandit.

    python scripts/train_contact_timing.py --seed 42

Splits the engagement log by customer, fits posteriors on the train side,
replays the held-out side, and saves the bandit to the versioned artifact
store. Also writes the small ``model_card.json`` the studio endpoint serves,
so the API never recomputes these numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from src.ml.contact_timing.artifacts import save_timing_bandit  # noqa: E402
from src.ml.contact_timing.evaluation import (  # noqa: E402
    format_replay_report,
    replay_evaluate,
    split_by_customer,
    train_bandit,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/contact_timing"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-fraction", type=float, default=0.3)
    parser.add_argument("--no-save", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    log_path = args.data / "engagement_log.csv"
    frame = pd.read_csv(log_path)
    train, heldout = split_by_customer(frame, eval_fraction=args.eval_fraction, seed=args.seed)
    print(f"train rows={len(train)}  held-out rows={len(heldout)}")

    bandit = train_bandit(train)
    report = replay_evaluate(bandit, heldout, seed=args.seed)
    print("\n=== offline replay evaluation (held-out customers) ===")
    print(format_replay_report(report))

    card = {
        "model_version": None,
        "train_rows": report.train_rows,
        "eval_rows": report.eval_rows,
        "eval_customers": report.eval_customers,
        "matched_rows": report.matched_rows,
        "policy_reward": report.policy_reward,
        "logging_reward": report.logging_reward,
        "lift_over_logging": report.lift_over_logging,
        "best_fixed_arm": report.best_fixed_arm,
        "best_fixed_arm_reward": report.best_fixed_arm_reward,
        "truth_mae": report.truth_mae,
        "seed": args.seed,
    }

    if not args.no_save:
        path, metadata = save_timing_bandit(
            bandit,
            metrics={
                "policy_reward": report.policy_reward,
                "logging_reward": report.logging_reward,
                "lift_over_logging": report.lift_over_logging,
                "best_fixed_arm_reward": report.best_fixed_arm_reward,
                "truth_mae": report.truth_mae,
            },
            notes=(
                f"segmented Thompson Sampling over 15 send-time arms, "
                f"grouped split seed={args.seed}"
            ),
        )
        card["model_version"] = metadata.model_version
        print(f"\nsaved {metadata.model_version} to {path}")

    card_path = args.data / "model_card.json"
    card_path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {card_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
