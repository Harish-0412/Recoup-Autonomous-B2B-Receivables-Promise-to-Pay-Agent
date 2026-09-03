"""Train, evaluate and save the recovery-probability model.

    python scripts/train_recovery_model.py --batch-size 6000 --seed 42

Trains three models on the same temporally split data -- a logistic baseline,
gradient-boosted trees, and a small neural net -- calibrates each on the
validation split, and reports them against each other and against the
rules-based scorer they are meant to replace. The best model by test AUC is
saved to the versioned artifact store.
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

from src.data.synthetic_generator import generate_batch  # noqa: E402
from src.ml.recovery.artifacts import save_recovery_model  # noqa: E402
from src.ml.recovery.dataset import build_dataset  # noqa: E402
from src.ml.recovery.evaluation import (  # noqa: E402
    compare_rankings,
    evaluate,
    format_calibration_curve,
    format_metrics_table,
    rules_based_scores,
)
from src.ml.recovery.explain import RecoveryExplainer, format_global_importance  # noqa: E402
from src.ml.recovery.models import (  # noqa: E402
    BASELINE_NAME,
    NEURAL_NAME,
    PRIMARY_NAME,
    build_gradient_boosting,
    build_logistic_regression,
    build_neural_network,
    train_model,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=6000, help="number of invoices")
    parser.add_argument("--customers", type=int, default=900)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--horizon-days", type=int, default=30)
    parser.add_argument(
        "--timeline-days",
        type=int,
        default=540,
        help="how far back invoices were flagged, so the split can be temporal",
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument(
        "--calibration",
        choices=["isotonic", "sigmoid", "none"],
        default="isotonic",
    )
    parser.add_argument("--out", type=Path, default=Path("data/recovery_model"))
    parser.add_argument("--no-save", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    calibration = None if args.calibration == "none" else args.calibration

    # --- data ---------------------------------------------------------------
    batch = generate_batch(
        batch_size=args.batch_size,
        customer_count=args.customers,
        seed=args.seed,
        horizon_days=args.horizon_days,
        as_of=args.as_of,
        timeline_days=args.timeline_days,
    )
    dataset = build_dataset(batch)
    summary = dataset.summary()

    print(f"generated {len(batch.invoices)} invoices, seed={args.seed}")
    print(
        f"  {len(batch.mature_invoices())} have a mature {args.horizon_days}-day label "
        f"({len(batch.invoices) - len(batch.mature_invoices())} still inside the horizon "
        "and therefore excluded)"
    )
    print(f"  features: {len(dataset.feature_columns)}")
    for name, info in summary["splits"].items():  # type: ignore[union-attr]
        print(
            f"  {name:<11} rows={info['rows']:<6} positive_rate={info['positive_rate']:.3f}  "
            f"{info['first_flag_date']} -> {info['last_flag_date']}"
        )
    print("  split is by flag date, so no model sees an invoice flagged after its test set")

    # --- train --------------------------------------------------------------
    specs = [
        (BASELINE_NAME, build_logistic_regression(args.seed)),
        (PRIMARY_NAME, build_gradient_boosting(args.seed)),
        (NEURAL_NAME, build_neural_network(args.seed)),
    ]

    trained = {}
    test_metrics = []
    validation_metrics = []
    for name, estimator in specs:
        model = train_model(
            name,
            estimator,
            dataset.train.features,
            dataset.train.labels,
            x_validation=dataset.validation.features,
            y_validation=dataset.validation.labels,
            calibration_method=calibration,
        )
        trained[name] = model
        test_metrics.append(
            evaluate(
                name,
                "test",
                dataset.test.labels,
                model.predict_proba(dataset.test.features),
                threshold=args.threshold,
            )
        )
        validation_metrics.append(
            evaluate(
                name,
                "validation",
                dataset.validation.labels,
                model.predict_proba(dataset.validation.features),
                threshold=args.threshold,
            )
        )

    # The incumbent, run through its own public entry point.
    rules_test = rules_based_scores(dataset.test.cases)
    rules_metrics = evaluate(
        "rules-based", "test", dataset.test.labels, rules_test, threshold=args.threshold
    )

    print("\n=== held-out test split (most recent invoices) ===")
    print(format_metrics_table([rules_metrics, *test_metrics]))

    baseline_auc = next(m.roc_auc for m in test_metrics if m.model_name == BASELINE_NAME)
    primary_auc = next(m.roc_auc for m in test_metrics if m.model_name == PRIMARY_NAME)
    neural_auc = next(m.roc_auc for m in test_metrics if m.model_name == NEURAL_NAME)

    print("\nmodel-choice notes:")
    if primary_auc >= baseline_auc:
        print(
            f"  gradient boosting beats the logistic baseline "
            f"({primary_auc:.3f} vs {baseline_auc:.3f}); the nonlinearity is real."
        )
    else:
        print(
            f"  the logistic baseline beats gradient boosting "
            f"({baseline_auc:.3f} vs {primary_auc:.3f}). The signal here is essentially "
            "linear -- ship the simpler model."
        )
    if neural_auc >= max(primary_auc, baseline_auc):
        print(f"  the neural net leads at {neural_auc:.3f}.")
    else:
        print(
            f"  the neural net does not win ({neural_auc:.3f} vs "
            f"{max(primary_auc, baseline_auc):.3f}) -- expected on a few thousand rows "
            "of tabular data, and worth stating rather than hiding."
        )

    # --- pick the model to ship --------------------------------------------
    best = max(test_metrics, key=lambda metrics: metrics.roc_auc)
    best_model = trained[best.model_name]
    print(f"\nselected: {best.model_name} (test AUC {best.roc_auc:.3f})")

    # --- head to head against the incumbent --------------------------------
    amounts = [case.invoice.outstanding for case in dataset.test.cases]
    comparison = compare_rankings(
        "test",
        dataset.test.labels,
        best_model.predict_proba(dataset.test.features),
        rules_test,
        amounts,
        challenger=best.model_name,
        incumbent="rules-based",
    )
    print("\n=== head to head vs the scorer it replaces ===")
    print(f"  {comparison.verdict()}")
    print(
        f"  rupees genuinely at risk surfaced in the top {comparison.k} of the "
        f"work queue: {comparison.challenger_value_at_risk_at_k:,.0f} vs "
        f"{comparison.incumbent_value_at_risk_at_k:,.0f}"
    )

    # --- calibration --------------------------------------------------------
    print(f"\n=== calibration ({args.calibration}, fitted on validation) ===")
    print(format_calibration_curve(best))
    print(
        f"  Brier={best.brier_score:.4f}  ECE={best.expected_calibration_error:.3f} "
        f"(rules-based: Brier={rules_metrics.brier_score:.4f}, "
        f"ECE={rules_metrics.expected_calibration_error:.3f})"
    )

    # --- explanations -------------------------------------------------------
    explainer = RecoveryExplainer(best_model)
    importance = explainer.global_importance(dataset.test.features)
    print("\n=== global SHAP importance ===")
    print(format_global_importance(importance))

    if explainer.available:
        sample_case = dataset.test.cases[0]
        from src.ml.features.recovery_features import build_recovery_features

        drivers = explainer.top_drivers(build_recovery_features(sample_case), limit=3)
        print(f"\nper-prediction drivers for {sample_case.invoice.invoice_id}:")
        for driver in drivers:
            print(
                f"  {driver.feature:<42} value={driver.value:<12.4f} "
                f"shap={driver.shap_contribution:+.4f}"
            )

    # --- persist ------------------------------------------------------------
    report = {
        "dataset": summary,
        "calibration": args.calibration,
        "threshold": args.threshold,
        "test": [json.loads(m.model_dump_json()) for m in [rules_metrics, *test_metrics]],
        "validation": [json.loads(m.model_dump_json()) for m in validation_metrics],
        "head_to_head": json.loads(comparison.model_dump_json()),
        "selected_model": best.model_name,
        "global_importance": importance,
    }

    if not args.no_save:
        path, metadata = save_recovery_model(
            best_model,
            train_rows=len(dataset.train),
            metrics={
                "roc_auc": best.roc_auc,
                "average_precision": best.average_precision,
                "f1": best.f1,
                "brier_score": best.brier_score,
                "expected_calibration_error": best.expected_calibration_error,
                "rules_based_roc_auc": rules_metrics.roc_auc,
            },
            notes=(
                f"{best.model_name}, {args.calibration} calibration on the validation split, "
                f"temporal split at {dataset.train_end}/{dataset.validation_end}"
            ),
        )
        report["model_version"] = metadata.model_version
        print(f"\nsaved {metadata.model_version} to {path}")

    report_path = args.out / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
