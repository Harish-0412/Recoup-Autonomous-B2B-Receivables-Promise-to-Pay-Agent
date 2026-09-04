"""Train, validate and save the receivables cash-forecast model.

    python scripts/train_cash_forecast.py --batch-size 6000 --seed 7

Fits per-segment empirical payment-lag tables on realised lags from a fresh
generated batch, calibrates the recovery scorer's probabilities with a Platt
scaler on the validation slice, and backtests windowed (7-day / 30-day) cash
forecasts on the held-out test slice.

Acceptance gates (see src/ml/cash_forecast/backtest.py) are checked on the
validation slice first. If they fail, the script retries automatically --
Platt calibration, then heavier segment pooling, then global-only lags --
and ships the first configuration that passes. If nothing passes, it writes
the evaluation report, saves nothing, and exits nonzero: an unvalidated
forecast must never become the artifact the API serves.
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

from app.core.domain import CaseSnapshot  # noqa: E402
from src.data.synthetic_generator import generate_batch  # noqa: E402
from src.ml.cash_forecast.artifacts import (  # noqa: E402
    CashForecastModel,
    save_cash_forecast_model,
)
from src.ml.cash_forecast.backtest import (  # noqa: E402
    BacktestReport,
    PlattScaler,
    fit_platt_scaler,
    run_backtest,
)
from src.ml.cash_forecast.dataset import LagDataset, LagObservation, build_lag_dataset  # noqa: E402
from src.ml.cash_forecast.lags import LagTables, fit_lag_tables  # noqa: E402
from src.ml.cash_forecast.simulate import DEFAULT_DRAWS  # noqa: E402
from src.ml.recovery.dataset import build_cases  # noqa: E402
from src.ml.recovery.dataset import features_frame as recovery_features_frame  # noqa: E402
from src.ml.recovery.evaluation import rules_based_scores  # noqa: E402
from src.ml.recovery.scorer import (  # noqa: E402
    ModelBasedScorer,
    RecoveryScorer,
    get_recovery_scorer,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=9000, help="number of invoices")
    parser.add_argument("--customers", type=int, default=1350)
    parser.add_argument("--seed", type=int, default=7, help="fresh seed, away from other models")
    parser.add_argument("--horizon-days", type=int, default=30)
    parser.add_argument(
        "--timeline-days",
        type=int,
        default=540,
        help="how far back invoices were flagged, so the split can be temporal",
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    parser.add_argument(
        "--backtest-draws",
        type=int,
        default=25_000,
        help="draws per bucket when backtesting (edges must be MC-stable)",
    )
    parser.add_argument("--min-segment-samples", type=int, default=30)
    parser.add_argument("--out", type=Path, default=Path("data/cash_forecast"))
    parser.add_argument("--no-save", action="store_true")
    return parser


def _score_cases(
    dataset: LagDataset,
    cases_by_id: dict[str, CaseSnapshot],
    scorer: RecoveryScorer,
) -> tuple[dict[str, float], dict[str, int]]:
    """Score every mature invoice once; return probs and realised labels.

    Batch path for the trained model (one predict_proba call, no per-row
    SHAP), per-case path for the rules. Either way the probabilities are the
    same numbers the serving path would produce for these invoices.
    """

    all_ids = [
        row.invoice_id
        for split in (dataset.train, dataset.validation, dataset.test)
        for row in split.rows
    ]
    ordered_cases = [cases_by_id[i] for i in all_ids]

    if isinstance(scorer, ModelBasedScorer) and scorer.is_available and scorer.model is not None:
        frame = recovery_features_frame(ordered_cases)
        raw = [float(p) for p in scorer.model.predict_proba(frame)]
    else:
        raw = [float(p) for p in rules_based_scores(ordered_cases)]

    probs = dict(zip(all_ids, raw, strict=True))
    labels: dict[str, int] = {}
    for split in (dataset.train, dataset.validation, dataset.test):
        for row in split.rows:
            labels[row.invoice_id] = 1 if row.recovered else 0
    return probs, labels


def _attempt(
    name: str,
    rows: list[LagObservation],
    probs: dict[str, float],
    tables: LagTables,
    scaler: PlattScaler | None,
    draws: int,
    seed: int,
) -> BacktestReport:
    report = run_backtest(rows, probs, tables, draws=draws, seed=seed, scaler=scaler)
    gates = report.gates()
    verdict = "PASS" if gates["passed"] else "FAIL"
    print(
        f"  [{verdict}] {name}: coverage_30d={gates['coverage_30d']:.3f} "
        f"(need >= {gates['required_coverage_30d']:.2f}), "
        f"bias_30d={gates['bias_30d']:+.3f} "
        f"(need |.| <= {gates['max_abs_bias_30d']:.2f}), "
        f"buckets={len(report.buckets)}"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    # --- data ---------------------------------------------------------------
    batch = generate_batch(
        batch_size=args.batch_size,
        customer_count=args.customers,
        seed=args.seed,
        horizon_days=args.horizon_days,
        as_of=args.as_of,
        timeline_days=args.timeline_days,
    )
    dataset = build_lag_dataset(batch)
    summary = dataset.summary()

    mature = batch.mature_invoices()
    cases_by_id = {case.invoice.invoice_id: case for case in build_cases(mature, batch.customers)}

    print(f"generated {len(batch.invoices)} invoices, seed={args.seed}")
    print(
        f"  {len(mature)} mature ({len(batch.invoices) - len(mature)} still inside the "
        "horizon and excluded)"
    )
    splits = summary["splits"]
    assert isinstance(splits, dict)
    for name, info in splits.items():
        assert isinstance(info, dict)
        print(
            f"  {name:<11} rows={info['rows']:<6} recovered={info['recovered_rows']:<6} "
            f"mean_lag={info['mean_lag_days']}d  "
            f"{info['first_flag_date']} -> {info['last_flag_date']}"
        )
    print("  split is by flag date, so no table sees an invoice flagged after its test set")

    # --- probabilities ------------------------------------------------------
    scorer = get_recovery_scorer()
    scorer_label = type(scorer).__name__
    probs, labels = _score_cases(dataset, cases_by_id, scorer)
    print(f"  scored {len(probs)} mature invoices with {scorer_label}")

    val_rows = list(dataset.validation.rows)
    val_probs = [probs[r.invoice_id] for r in val_rows]
    val_labels = [labels[r.invoice_id] for r in val_rows]
    scaler = fit_platt_scaler(val_probs, val_labels)
    print(
        f"  Platt scaler fitted on {len(val_rows)} validation rows: "
        f"slope={scaler.slope:.3f} intercept={scaler.intercept:+.3f}"
    )

    train_lags = dataset.train.recovered_lags
    print(f"  {len(train_lags)} realised lags available for fitting")

    # --- validation attempts, easiest fix first ------------------------------
    attempts: list[dict[str, object]] = []
    winner: tuple[str, LagTables, PlattScaler | None, BacktestReport] | None = None

    tables = fit_lag_tables(
        train_lags, min_segment_samples=args.min_segment_samples, horizon_days=args.horizon_days
    )
    print(
        f"  {len(tables.segments)} segments kept, {len(tables.pooled_segments)} pooled "
        f"(min_samples={args.min_segment_samples})"
    )
    report = _attempt(
        "A raw probs + segment lags", val_rows, probs, tables, None, args.backtest_draws, args.seed
    )
    attempts.append({"name": "A", "gates": report.gates()})
    if report.gates()["passed"]:
        winner = ("A", tables, None, report)

    if winner is None:
        report = _attempt(
            "B Platt probs + segment lags",
            val_rows,
            probs,
            tables,
            scaler,
            args.backtest_draws,
            args.seed,
        )
        attempts.append({"name": "B", "gates": report.gates()})
        if report.gates()["passed"]:
            winner = ("B", tables, scaler, report)

    if winner is None:
        pooled = fit_lag_tables(
            train_lags,
            min_segment_samples=args.min_segment_samples * 4,
            horizon_days=args.horizon_days,
        )
        print(
            f"  retry pools harder: {len(pooled.segments)} kept, {len(pooled.pooled_segments)} pooled"
        )
        report = _attempt(
            "C Platt probs + heavy pooling",
            val_rows,
            probs,
            pooled,
            scaler,
            args.backtest_draws,
            args.seed,
        )
        attempts.append({"name": "C", "gates": report.gates()})
        if report.gates()["passed"]:
            winner = ("C", pooled, scaler, report)

    if winner is None:
        pooled = fit_lag_tables(
            train_lags, min_segment_samples=10**9, horizon_days=args.horizon_days
        )
        report = _attempt(
            "D Platt probs + global-only lags",
            val_rows,
            probs,
            pooled,
            scaler,
            args.backtest_draws,
            args.seed,
        )
        attempts.append({"name": "D", "gates": report.gates()})
        if report.gates()["passed"]:
            winner = ("D", pooled, scaler, report)

    if winner is None:
        print("\nNO CONFIGURATION PASSED THE GATES -- saving nothing.")
        print("Refusing to ship an unvalidated forecast is the feature working, not failing:")
        print("rerun with a larger --batch-size or a different --seed and compare attempts.")
        _write_report(args, summary, attempts, winner_report=report, scorer_label=scorer_label)
        return 1

    name, final_tables, final_scaler, _ = winner
    print(f"\nselected attempt {name} (first to pass on validation)")

    # --- held-out test backtest, once, with the frozen winner -----------------
    test_rows = list(dataset.test.rows)
    test_report = run_backtest(
        test_rows,
        probs,
        final_tables,
        draws=args.backtest_draws,
        seed=args.seed + 1_000,
        scaler=final_scaler,
    )
    gates = test_report.gates()
    print("\n=== held-out test slice (most recent invoices) ===")
    for window in (7, 30):
        print(
            f"  {window:>2}d  coverage={test_report.coverage[window]:.3f}  "
            f"bias={test_report.bias[window]:+.3f}  "
            f"mae=Rs {test_report.mae[window]:,.0f}  rmse=Rs {test_report.rmse[window]:,.0f}  "
            f"realised=Rs {test_report.realised_total[window]:,.0f}  "
            f"forecast_mean=Rs {test_report.forecast_mean_total[window]:,.0f}"
        )
    print(
        f"  test gates: {'PASS' if gates['passed'] else 'FAIL'} "
        f"(coverage_30d={gates['coverage_30d']:.3f}, bias_30d={gates['bias_30d']:+.3f})"
    )
    if not gates["passed"]:
        print(
            "  winner passed validation but failed test -- saving nothing; investigate, do not ship."
        )
        _write_report(
            args,
            summary,
            attempts,
            winner_report=None,
            scorer_label=scorer_label,
            test_gates=gates,
        )
        return 1

    # --- persist ---------------------------------------------------------------
    model = CashForecastModel(
        lags=final_tables,
        scaler=final_scaler if final_scaler is not None else PlattScaler(slope=1.0, intercept=0.0),
        scaler_fitted_rows=len(val_rows) if final_scaler is not None else 0,
        min_segment_samples=final_tables.min_segment_samples,
    )
    metrics = {
        "coverage_7d": test_report.coverage[7],
        "coverage_30d": test_report.coverage[30],
        "bias_7d": test_report.bias[7],
        "bias_30d": test_report.bias[30],
        "mae_30d": test_report.mae[30],
        "rmse_30d": test_report.rmse[30],
    }
    _write_report(
        args,
        summary,
        attempts,
        winner_report=test_report,
        scorer_label=scorer_label,
        test_gates=gates,
        attempt=name,
        scaler={"slope": model.scaler.slope, "intercept": model.scaler.intercept},
    )

    if not args.no_save:
        path, metadata = save_cash_forecast_model(
            model,
            train_rows=final_tables.train_rows,
            metrics=metrics,
            notes=(
                f"attempt {name}, Platt scaler on validation, "
                f"temporal split at {dataset.train_end}/{dataset.validation_end}"
            ),
        )
        print(f"\nsaved {metadata.model_version} to {path}")
        card = {
            "model_version": metadata.model_version,
            "attempt": name,
            "probability_source": scorer_label,
            "platt_scaler": {"slope": model.scaler.slope, "intercept": model.scaler.intercept},
            "test_rows": sum(len(b) for b in [test_rows]),
            "test_buckets": len(test_report.buckets),
            "coverage": {str(k): v for k, v in test_report.coverage.items()},
            "bias": {str(k): v for k, v in test_report.bias.items()},
            "mae": {str(k): v for k, v in test_report.mae.items()},
            "rmse": {str(k): v for k, v in test_report.rmse.items()},
            "realised_total": {str(k): v for k, v in test_report.realised_total.items()},
            "forecast_mean_total": {str(k): v for k, v in test_report.forecast_mean_total.items()},
            "segments_kept": sorted(final_tables.segments),
            "segments_pooled": sorted(final_tables.pooled_segments),
        }
        card_path = args.out / "model_card.json"
        card_path.write_text(json.dumps(card, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"wrote {card_path}")
    return 0


def _write_report(
    args: argparse.Namespace,
    summary: dict[str, object],
    attempts: list[dict[str, object]],
    *,
    winner_report: BacktestReport | None,
    scorer_label: str,
    test_gates: dict[str, object] | None = None,
    attempt: str | None = None,
    scaler: dict[str, float] | None = None,
) -> None:
    buckets = []
    if winner_report is not None:
        buckets = [
            {
                "bucket_start": b.bucket_start,
                "n_invoices": b.n_invoices,
                "realised": b.realised,
                "mean": b.mean,
                "p5": b.p5,
                "p95": b.p95,
            }
            for b in winner_report.buckets
        ]
    report = {
        "dataset": summary,
        "probability_source": scorer_label,
        "draws": args.draws,
        "min_segment_samples": args.min_segment_samples,
        "seed": args.seed,
        "attempts": attempts,
        "selected_attempt": attempt,
        "platt_scaler": scaler,
        "test_gates": test_gates,
        "test_coverage": dict(winner_report.coverage) if winner_report else None,
        "test_bias": dict(winner_report.bias) if winner_report else None,
        "test_buckets": buckets,
    }
    report_path = args.out / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {report_path}")


if __name__ == "__main__":
    raise SystemExit(main())
