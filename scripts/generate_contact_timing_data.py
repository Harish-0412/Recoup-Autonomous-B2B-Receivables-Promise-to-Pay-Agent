"""Generate the engagement log the contact-timing bandit trains on.

    python scripts/generate_contact_timing_data.py --customers 900 --seed 42

Writes ``data/contact_timing/engagement_log.csv`` plus a build manifest.
Regenerating with the same seed byte-reproduces the log; the bandit trains
from these files, never from in-memory fixtures, so the training run below is
checkable rather than merely claimed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ml.contact_timing.dataset import build_engagement_log, write_dataset  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customers", type=int, default=1200)
    parser.add_argument("--touches-per-customer", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/contact_timing"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    log = build_engagement_log(
        customer_count=args.customers,
        touches_per_customer=args.touches_per_customer,
        seed=args.seed,
    )
    out = write_dataset(log, args.out)

    print(f"customers={log.customers} touches={log.touches} seed={log.seed}")
    print(f"  overall response rate: {log.frame['reward'].mean():.3f}")
    print(f"  segments: {log.frame['segment'].nunique()}")
    print(f"wrote {out / 'engagement_log.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
