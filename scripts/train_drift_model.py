"""Train, evaluate and save the payment-behavior drift model.

    python scripts/train_drift_model.py --seed 42

Trains an Isolation Forest on *real* repayment behavior -- the UCI Default
of Credit Card Clients dataset (30k customers, CC BY 4.0, downloaded on
first run into ``data/drift/raw``) -- fit on customers who did not default
(the working definition of normal). Because drift has no ground-truth
label, the evaluation is three honest checks instead of an accuracy
number: flag-rate calibration on holdout normals, forward-default lift of
flags vs non-flags (a proxy, reported as such), and recall on customers
degraded on purpose (the only check with real ground truth).

The frozen score threshold ships inside the artifact, so serving never
re-derives it from live data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.model_selection import train_test_split

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.ml.config import MLSettings  # noqa: E402
from src.ml.drift.artifacts import save_drift_model  # noqa: E402
from src.ml.drift.dataset import (  # noqa: E402
    SOURCE_DOI,
    SOURCE_LICENSE,
    TARGET_COL,
    build_feature_frame,
    load_uci_frame,
)
from src.ml.drift.evaluation import (  # noqa: E402
    flag_rate,
    injected_drift_recall,
    proxy_precision,
)
from src.ml.drift.features import FEATURE_COLUMNS  # noqa: E402
from src.ml.drift.models import PRIMARY_NAME, fit_drift_model  # noqa: E402
from src.ml.drift.service import top_drivers_for  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--contamination", type=float, default=0.10)
    parser.add_argument(
        "--alternatives",
        type=float,
        nargs="*",
        default=[0.05, 0.15],
        help="other contamination levels reported for comparison, not shipped",
    )
    parser.add_argument("--test-size", type=float, default=0.20)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--data-dir", type=Path, default=Path("data/drift/raw"))
    parser.add_argument("--out", type=Path, default=Path("data/drift_model"))
    parser.add_argument("--no-save", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    MLSettings()

    # --- data -----------------------------------------------------------
    frame = load_uci_frame(args.data_dir)
    print(f"loaded {len(frame)} customers x {frame.shape[1]} columns from UCI (CC BY 4.0)")
    proxy = frame[TARGET_COL].astype(int)
    print(f"proxy default rate: {proxy.mean():.4f}")

    train_idx, hold_idx = train_test_split(
        np.arange(len(frame)),
        test_size=args.test_size,
        random_state=args.seed,
        stratify=proxy,
    )
    train_frame = frame.iloc[train_idx].reset_index(drop=True)
    hold_frame = frame.iloc[hold_idx].reset_index(drop=True)
    hold_proxy = proxy.iloc[hold_idx].reset_index(drop=True)

    train_features = build_feature_frame(train_frame)
    hold_features = build_feature_frame(hold_frame)
    train_normals = train_features[train_frame[TARGET_COL].to_numpy() == 0]
    print(
        f"train {len(train_features)} ({len(train_normals)} normals), "
        f"holdout {len(hold_features)}"
    )

    # --- models ----------------------------------------------------------
    levels = [args.contamination, *[a for a in args.alternatives if a != args.contamination]]
    results = []
    fitted = {}
    for level in levels:
        model = fit_drift_model(
            train_normals,
            contamination=level,
            n_estimators=args.n_estimators,
            seed=args.seed,
        )
        fitted[level] = model
        hold_normals = hold_features[hold_proxy.to_numpy() == 0]
        precision = proxy_precision(model, hold_features, hold_proxy)
        results.append(
            {
                "contamination": level,
                "threshold": model.threshold,
                "holdout_flag_rate": flag_rate(model, hold_features),
                "holdout_normal_flag_rate": flag_rate(model, hold_normals),
                **precision,
            }
        )
        print(
            f"contamination={level:.2f} threshold={model.threshold:+.6f} "
            f"flag_rate={results[-1]['holdout_flag_rate']:.3f} "
            f"normal_flag_rate={results[-1]['holdout_normal_flag_rate']:.3f} "
            f"lift={precision['lift']:.2f}x"
        )

    primary = fitted[args.contamination]

    # --- injected-drift recall (ground truth by construction) ------------
    recall = injected_drift_recall(primary, hold_frame, max_customers=2000, seed=args.seed)
    print(
        f"injected drift: {recall['newly_flagged']:.0f}/{recall['degraded']:.0f} "
        f"newly flagged (recall {recall['recall']:.3f})"
    )

    # --- global drivers: what distinguishes flagged holdout customers ----
    flagged = hold_features[primary.is_flagged(hold_features)]
    deviations: Counter[str] = Counter()
    for _, row in flagged.iterrows():
        feats = {c: float(row[c]) for c in FEATURE_COLUMNS}
        for driver in top_drivers_for(primary, feats, limit=3):
            deviations[driver.feature] += 1
    total_votes = max(sum(deviations.values()), 1)
    global_drivers = [
        {
            "feature": name,
            "flagged_share": round(count / len(flagged), 4) if len(flagged) else 0.0,
            "vote_share": round(count / total_votes, 4),
        }
        for name, count in deviations.most_common()
    ]

    # --- persist ----------------------------------------------------------
    report: dict = {
        "model": PRIMARY_NAME,
        "seed": args.seed,
        "source": {
            "dataset": "UCI Default of Credit Card Clients (id 350)",
            "doi": SOURCE_DOI,
            "license": SOURCE_LICENSE,
            "rows": len(frame),
        },
        "split": {"train_rows": len(train_features), "holdout_rows": len(hold_features)},
        "shipped_contamination": args.contamination,
        "results": results,
        "injected_drift": recall,
        "global_drivers": global_drivers,
        "limitations": [
            "Monthly training grain vs trailing-90-day serving grain; schema is rate-based to stay comparable.",
            "default-next-month is a forward-risk proxy, not drift ground truth.",
            "Disputes are excluded from the model and handled by the deterministic dispute path.",
            "Consumer credit behavior stands in for B2B receivables until webhook-confirmed outcomes accumulate.",
        ],
    }

    if not args.no_save:
        path, metadata = save_drift_model(
            primary,
            train_rows=len(train_normals),
            metrics={
                "holdout_flag_rate": results[0]["holdout_flag_rate"],
                "holdout_normal_flag_rate": results[0]["holdout_normal_flag_rate"],
                "proxy_lift": results[0]["lift"],
                "injected_recall": recall["recall"],
            },
            notes=(
                f"{PRIMARY_NAME} on UCI normals, contamination {args.contamination}, "
                f"threshold {primary.threshold:+.4f}"
            ),
        )
        report["model_version"] = metadata.model_version
        print(f"\nsaved {metadata.model_version} to {path}")

    report_path = args.out / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")

    card = {
        "model_version": report.get("model_version"),
        "shipped_model": PRIMARY_NAME,
        "contamination": args.contamination,
        "threshold": primary.threshold,
        "test_rows": len(hold_features),
        "source": "evaluation_report.json",
        "results": results,
        "injected_drift": recall,
        "global_drivers": global_drivers,
    }
    card_path = args.out / "model_card.json"
    card_path.write_text(json.dumps(card, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {card_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
