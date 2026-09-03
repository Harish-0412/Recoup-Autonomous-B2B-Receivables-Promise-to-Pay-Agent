"""Build the labelled corpus, train Stage C, evaluate it, and save the artifact.

    python scripts/train_reply_classifier.py --size 1200 --seed 42

Writes the trained model to the versioned artifact store and a JSON evaluation
report next to it. Both split strategies are reported: `grouped` (no template
spans two splits -- the honest number) and `random` (the conventional
stratified split, which scores higher because near-identical phrasings appear
in both train and test).
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

from src.ml.config import MLSettings  # noqa: E402
from src.ml.reply.cascade import threshold_for_intent  # noqa: E402
from src.ml.reply.classifier import ReplyIntentClassifier, save_classifier  # noqa: E402
from src.ml.reply.dataset import (  # noqa: E402
    SplitName,
    audit_corpus,
    build_corpus,
    corpus_texts_and_labels,
)
from src.ml.reply.evaluation import (  # noqa: E402
    aggregate_classification_reports,
    calibration_report,
    cascade_report,
    classification_report,
    entity_report,
    format_classification_report,
    format_confusion_matrix,
    format_repeated_evaluation,
)
from src.ml.schemas import IntentLabel  # noqa: E402


def _intent_thresholds(base: float) -> dict[str, float]:
    """The per-intent bars the cascade actually routes on.

    Read from ``threshold_for_intent`` rather than restated here, so the
    evaluation cannot quietly measure a different policy than the one that
    ships.
    """

    return {label.value: threshold_for_intent(label, base) for label in IntentLabel}


def _train_and_evaluate(*, size: int, seed: int, strategy: str, threshold: float, as_of: date):
    """Train on one split strategy and return (model, corpus, reports)."""

    corpus = build_corpus(size=size, seed=seed, reference_date=as_of, split_strategy=strategy)
    train = corpus.for_split(SplitName.TRAIN)
    test = corpus.for_split(SplitName.TEST)

    x_train, y_train = corpus_texts_and_labels(train)
    x_test, y_test = corpus_texts_and_labels(test)

    model = ReplyIntentClassifier.fit(x_train, y_train, seed=seed)

    probabilities = model.predict_proba(x_test)
    classes = model.classes
    predictions = [classes[int(row.argmax())] for row in probabilities]
    confidences = [float(row.max()) for row in probabilities]

    # The validation split is what the calibration method was selected on; it is
    # reported here so that choice stays checkable rather than folklore.
    x_val, y_val = corpus_texts_and_labels(corpus.for_split(SplitName.VAL))
    val_probabilities = model.predict_proba(x_val)
    val_predictions = [classes[int(row.argmax())] for row in val_probabilities]
    val_confidences = [float(row.max()) for row in val_probabilities]

    reports = {
        "classification": classification_report(y_test, predictions),
        "calibration": calibration_report(y_test, predictions, confidences),
        "cascade": cascade_report(
            y_test,
            predictions,
            confidences,
            threshold=threshold,
            intent_thresholds=_intent_thresholds(threshold),
        ),
        "validation_classification": classification_report(y_val, val_predictions),
        "validation_calibration": calibration_report(y_val, val_predictions, val_confidences),
        "validation_cascade": cascade_report(
            y_val,
            val_predictions,
            val_confidences,
            threshold=threshold,
            intent_thresholds=_intent_thresholds(threshold),
        ),
    }
    return model, corpus, reports, (y_test, predictions, confidences)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--size",
        type=int,
        # 94 templates x ~15 renderings each. Adding templates without raising
        # this thins every template's representation and costs accuracy.
        default=1400,
        help="corpus size (800-1500 recommended)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help=(
            "independent grouped splits to average over. A single split leaves "
            "only a handful of templates in test, so one number is noise"
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="cascade confidence threshold (default: ML_CONFIDENCE_THRESHOLD)",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date(2026, 9, 1),
        help="reference date relative dates resolve against",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/reply_model"),
        help="directory for the evaluation report and spot-check sample",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="evaluate without writing a model artifact",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = MLSettings()
    threshold = args.threshold if args.threshold is not None else settings.ml_confidence_threshold
    args.out.mkdir(parents=True, exist_ok=True)

    # --- corpus quality gate ------------------------------------------------
    corpus_for_audit = build_corpus(size=args.size, seed=args.seed, reference_date=args.as_of)
    audit, sample = audit_corpus(corpus_for_audit, sample_fraction=0.10)
    sample_path = args.out / "spot_check_sample.jsonl"
    sample_path.write_text(
        "\n".join(example.model_dump_json() for example in sample) + "\n",
        encoding="utf-8",
    )

    print(f"corpus: {len(corpus_for_audit.examples)} examples, seed={args.seed}")
    print(f"  intent balance: {corpus_for_audit.intent_counts()}")
    print(
        f"  automated audit: {audit.sampled}/{audit.total} sampled, "
        f"{len(audit.findings)} findings, {audit.opt_out_guard_misses} opt-out guard misses"
    )
    for finding in audit.findings[:10]:
        print(f"    ! {finding.template_id} {finding.intent.value}: {finding.problem}")
    print(f"  spot-check sample for human review written to {sample_path}")

    # --- train under both split strategies ----------------------------------
    results = {}
    for strategy in ("grouped", "random"):
        model, corpus, reports, raw = _train_and_evaluate(
            size=args.size,
            seed=args.seed,
            strategy=strategy,
            threshold=threshold,
            as_of=args.as_of,
        )
        results[strategy] = (model, corpus, reports, raw)

    grouped_model, grouped_corpus, grouped_reports, _ = results["grouped"]
    random_reports = results["random"][2]

    print("\n=== Stage C, grouped split (no template spans two splits) ===")
    print(format_classification_report(grouped_reports["classification"]))
    print("\nconfusion matrix (rows = true, columns = predicted)")
    print(format_confusion_matrix(grouped_reports["classification"]))

    print("\ntop confusions:")
    for true_label, predicted_label, count in grouped_reports["classification"].top_confusions():
        print(f"  {true_label} -> {predicted_label}: {count}")

    print(f"\ncalibration: {grouped_reports['calibration'].summary()}")
    for bucket in grouped_reports["calibration"].buckets:
        print(
            f"  [{bucket.lower:.1f}, {bucket.upper:.1f})  n={bucket.count:<4d} "
            f"stated={bucket.mean_confidence:.3f}  observed={bucket.observed_accuracy:.3f}"
        )

    cascade = grouped_reports["cascade"]
    print(f"\ncascade at threshold {cascade.threshold:.2f}:")
    print(
        f"  Stage C resolves {cascade.resolved_by_model}/{cascade.total} "
        f"({cascade.resolution_rate:.1%}) at {cascade.model_accuracy_on_resolved:.3f} accuracy"
    )
    print(
        f"  escalated to Stage A: {cascade.escalated_to_llm} "
        f"({cascade.escalation_rate:.1%}); Stage C would have scored "
        f"{cascade.model_accuracy_on_escalated:.3f} on those"
    )

    if cascade.intent_thresholds:
        raised = ", ".join(
            f"{label} >= {bar:.2f}" for label, bar in sorted(cascade.intent_thresholds.items())
        )
        print(f"  raised bars: {raised}")
    print("\n  precision on what Stage C actually acts on (kept, not escalated):")
    for label in sorted(cascade.kept_by_intent):
        kept = cascade.kept_by_intent[label]
        dropped = cascade.escalated_by_intent.get(label, 0)
        print(
            f"    {label:<24} P={cascade.kept_precision_by_intent[label]:.3f} "
            f"on {kept:>3} kept ({dropped} escalated)"
        )

    repeated = None
    if args.repeats > 1:
        reports = []
        seeds = [args.seed + offset for offset in range(args.repeats)]
        for seed in seeds:
            _, _, seed_reports, _ = _train_and_evaluate(
                size=args.size,
                seed=seed,
                strategy="grouped",
                threshold=threshold,
                as_of=args.as_of,
            )
            reports.append(seed_reports["classification"])
        repeated = aggregate_classification_reports(reports, seeds)
        print()
        print("=== repeated grouped splits (the number to quote) ===")
        print(format_repeated_evaluation(repeated))
        dispute = repeated.for_label("DISPUTE")
        if dispute is not None:
            print()
            print(
                f"  DISPUTE precision {dispute.precision_mean:.3f} "
                f"+- {dispute.precision_std:.3f} "
                f"(range {dispute.precision_min:.2f}-{dispute.precision_max:.2f}). "
                "The spread is wide because a grouped split leaves few DISPUTE "
                "templates in test, which is why the raised cascade bar -- not "
                "this number -- is what protects the decision."
            )

    entities = entity_report(grouped_corpus.examples)
    print("\nentity extraction over the whole corpus:")
    print(
        f"  amounts {entities.amount_correct}/{entities.amount_expected} "
        f"({entities.amount_accuracy:.3f}), {entities.amount_false_positives} false positives"
    )
    print(
        f"  dates   {entities.date_correct}/{entities.date_expected} "
        f"({entities.date_accuracy:.3f}), {entities.date_false_positives} false positives"
    )
    print(
        f"  of those false positives, "
        f"{entities.amount_false_positives_actionable} amounts and "
        f"{entities.date_false_positives_actionable} dates survive the intent gate "
        "and could reach a promise record"
    )

    print(
        f"\nsplit-strategy comparison: grouped macro-F1="
        f"{grouped_reports['classification'].macro_f1:.3f}  "
        f"random macro-F1={random_reports['classification'].macro_f1:.3f}"
    )
    print("  The random figure is the optimistic one; grouped is what generalises.")

    val_cascade = grouped_reports["validation_cascade"]
    print(
        "\nvalidation split (used to choose the calibration method): "
        f"accuracy={grouped_reports['validation_classification'].accuracy:.3f} "
        f"ECE={grouped_reports['validation_calibration'].expected_calibration_error:.3f} "
        f"resolve={val_cascade.resolution_rate:.1%} "
        f"acc_kept={val_cascade.model_accuracy_on_resolved:.3f}"
    )

    # --- persist ------------------------------------------------------------
    report_payload = {
        "corpus": {
            "size": len(grouped_corpus.examples),
            "seed": args.seed,
            "dataset_version": grouped_corpus.dataset_version,
            "intent_counts": grouped_corpus.intent_counts(),
            "audit": json.loads(audit.model_dump_json()),
        },
        "repeated": json.loads(repeated.model_dump_json()) if repeated else None,
        "threshold": threshold,
        "grouped_split": {
            name: json.loads(report.model_dump_json()) for name, report in grouped_reports.items()
        },
        "random_split": {
            name: json.loads(report.model_dump_json()) for name, report in random_reports.items()
        },
        "entities": json.loads(entities.model_dump_json()),
    }

    if not args.no_save:
        metrics = {
            "accuracy": grouped_reports["classification"].accuracy,
            "macro_f1": grouped_reports["classification"].macro_f1,
            "weighted_f1": grouped_reports["classification"].weighted_f1,
            "expected_calibration_error": grouped_reports["calibration"].expected_calibration_error,
            "cascade_resolution_rate": cascade.resolution_rate,
        }
        path, metadata = save_classifier(
            grouped_model,
            train_rows=len(grouped_corpus.for_split(SplitName.TRAIN)),
            metrics=metrics,
            notes=(
                f"Trained on {grouped_corpus.dataset_version} (seed {args.seed}), "
                "grouped-by-template split."
            ),
        )
        report_payload["model_version"] = metadata.model_version
        print(f"\nsaved model {metadata.model_version} to {path}")

    report_path = args.out / "evaluation_report.json"
    report_path.write_text(json.dumps(report_payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
