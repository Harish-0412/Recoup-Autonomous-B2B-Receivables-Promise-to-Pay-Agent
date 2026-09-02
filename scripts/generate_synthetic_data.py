"""Generate and export a reproducible synthetic batch.

    python scripts/generate_synthetic_data.py --batch-size 600 --seed 42 --out data/synthetic/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.synthetic_generator import batch_summary, generate_batch  # noqa: E402
from src.ml.data.export import export_batch  # noqa: E402


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=600, help="number of invoices")
    parser.add_argument(
        "--customers",
        type=int,
        default=None,
        help="number of customers (default: batch-size / 4, minimum 20)",
    )
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=30,
        help="recovery horizon the label is defined against",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        default=date(2026, 9, 1),
        help="scoring date (YYYY-MM-DD); fixed by default so runs stay reproducible",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/synthetic"),
        help="output directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    batch = generate_batch(
        batch_size=args.batch_size,
        customer_count=args.customers,
        seed=args.seed,
        horizon_days=args.horizon_days,
        as_of=args.as_of,
    )

    manifest_path = export_batch(
        batch.customers,
        batch.invoices,
        args.out,
        seed=batch.seed,
        horizon_days=batch.horizon_days,
        as_of=batch.as_of,
        generator_version=batch.generator_version,
        reply_seed_examples=batch.reply_seed_examples,
    )

    summary = batch_summary(batch)
    print(f"seed={batch.seed} as_of={batch.as_of} horizon={batch.horizon_days}d")
    print(json.dumps(summary, indent=2))
    print(f"wrote {manifest_path.parent.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
