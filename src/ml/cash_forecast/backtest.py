"""Temporal backtest: does the forecast distribution earn its intervals?

Method: group a held-out slice into flag-date buckets (each bucket is one
"book" the forecaster would have faced), forecast each bucket with the fitted
lag tables and per-invoice probabilities, and compare the realised windowed
totals against the simulated distributions:

- **coverage**: fraction of buckets whose realised total lands inside [p5, p95].
  A calibrated 90% interval covers ~90% of buckets.
- **bias**: mean over buckets of (forecast_mean - realised) / realised.
  Positive means the forecast promises more cash than arrives.
- **mae / rmse**: absolute and squared error of the forecast mean, in rupees.

Probability calibration lives one layer down: the recovery scorer's raw
probabilities are passed through a Platt scaler (1-D logistic on the log-odds)
fitted on the validation slice and frozen before the test backtest. Fitting
the scaler on validation and evaluating on test keeps the test slice honest --
the scaler never sees the invoices it is judged on.

Acceptance gates (checked by the training script, with automatic retries):

- ``coverage_30d >= 0.75`` -- the 30-day 90% interval must cover most buckets.
- ``abs(bias_30d) <= 0.35`` -- the mean must not drift more than 35% off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.ml.cash_forecast.dataset import LagObservation
from src.ml.cash_forecast.lags import LagTables
from src.ml.cash_forecast.simulate import ForecastInput, forecast_cash

#: A bucket is one pseudo-book. 14-day buckets balance bucket count against
#: bucket size on a ~540-day timeline.
BUCKET_DAYS = 14

#: Acceptance gates, per the module docstring.
MIN_COVERAGE_30D = 0.75
MAX_ABS_BIAS_30D = 0.35


@dataclass(frozen=True)
class PlattScaler:
    """1-D logistic calibration of recovery probabilities. a,b frozen at fit."""

    slope: float
    intercept: float

    def calibrate(self, p: float) -> float:
        """Map a raw probability through the fitted logistic."""

        clipped = min(max(p, 1e-6), 1.0 - 1e-6)
        logit = math.log(clipped / (1.0 - clipped))
        adjusted = self.slope * logit + self.intercept
        return 1.0 / (1.0 + math.exp(-adjusted))


def fit_platt_scaler(
    probs: list[float],
    labels: list[int],
    *,
    l2: float = 1.0,
) -> PlattScaler:
    """Fit the scaler on validation (probs, realised labels).

    Closed-form-free: one Newton loop on the 1-D logistic loss with a small
    L2 pull toward the identity (slope=1, intercept=0), so a tiny validation
    slice cannot drag the calibrator somewhere wild. Pure numpy, no sklearn
    needed at this dimensionality.
    """

    if len(probs) != len(labels) or not probs:
        raise ValueError("need at least one (prob, label) pair to fit the scaler")

    xs = np.array(
        [
            math.log(min(max(p, 1e-6), 1.0 - 1e-6) / (1.0 - min(max(p, 1e-6), 1.0 - 1e-6)))
            for p in probs
        ]
    )
    ys = np.array(labels, dtype=float)

    theta = np.array([1.0, 0.0])  # slope, intercept (start at identity)
    design = np.column_stack([xs, np.ones_like(xs)])
    for _ in range(100):
        logits = design @ theta
        preds = 1.0 / (1.0 + np.exp(-logits))
        grad = design.T @ (preds - ys) + l2 * (theta - np.array([1.0, 0.0]))
        w = preds * (1.0 - preds)
        hessian = (design * w[:, None]).T @ design + l2 * np.eye(2)
        step = np.linalg.solve(hessian, grad)
        theta = theta - step
        if float(np.max(np.abs(step))) < 1e-8:
            break

    return PlattScaler(slope=float(theta[0]), intercept=float(theta[1]))


@dataclass(frozen=True)
class BucketResult:
    """One bucket: realised windowed totals vs the forecast distribution."""

    bucket_start: str
    n_invoices: int
    realised: dict[int, float]
    mean: dict[int, float]
    p5: dict[int, float]
    p95: dict[int, float]


@dataclass(frozen=True)
class BacktestReport:
    """Bucket-level verdicts aggregated per window."""

    windows: tuple[int, ...]
    buckets: tuple[BucketResult, ...]
    coverage: dict[int, float]
    bias: dict[int, float]
    mae: dict[int, float]
    rmse: dict[int, float]
    realised_total: dict[int, float]
    forecast_mean_total: dict[int, float]

    def gates(self) -> dict[str, object]:
        """Acceptance verdict. Ship only when every gate passes."""

        coverage_30 = self.coverage.get(30, 0.0)
        bias_30 = self.bias.get(30, float("inf"))
        passed = coverage_30 >= MIN_COVERAGE_30D and abs(bias_30) <= MAX_ABS_BIAS_30D
        return {
            "passed": passed,
            "coverage_30d": round(coverage_30, 4),
            "bias_30d": round(bias_30, 4),
            "required_coverage_30d": MIN_COVERAGE_30D,
            "max_abs_bias_30d": MAX_ABS_BIAS_30D,
        }


def _realised_windows(
    rows: list[LagObservation],
    *,
    windows: tuple[int, ...],
) -> dict[int, float]:
    totals = {w: 0.0 for w in windows}
    for row in rows:
        if not row.recovered or row.lag_days < 0:
            continue
        for window in windows:
            if row.lag_days <= window:
                totals[window] += row.amount
    return totals


def run_backtest(
    rows: list[LagObservation],
    probs: dict[str, float],
    tables: LagTables,
    *,
    windows: tuple[int, ...] = (7, 30),
    draws: int = 10_000,
    seed: int = 0,
    scaler: PlattScaler | None = None,
    bucket_days: int = BUCKET_DAYS,
) -> BacktestReport:
    """Forecast each flag-date bucket and score the distributions.

    ``probs`` maps invoice_id to P(recovery within the horizon) -- the same
    scorer output the serving path uses. ``scaler``, when given, calibrates
    those probabilities first (fitted on validation, frozen for test).
    """

    if not rows:
        raise ValueError("need at least one observation to backtest")

    ordered = sorted(rows, key=lambda row: (row.flagged_date, row.invoice_id))
    buckets: list[list[LagObservation]] = []
    current: list[LagObservation] = []
    bucket_start = ordered[0].flagged_date
    for row in ordered:
        if (row.flagged_date - bucket_start).days >= bucket_days and current:
            buckets.append(current)
            current = []
            bucket_start = row.flagged_date
        current.append(row)
    if current:
        buckets.append(current)

    bucket_results: list[BucketResult] = []
    for index, bucket in enumerate(buckets):
        inputs = [
            ForecastInput(
                invoice_id=row.invoice_id,
                amount=row.amount,
                p_recovery=(
                    scaler.calibrate(probs.get(row.invoice_id, 0.0))
                    if scaler is not None
                    else probs.get(row.invoice_id, 0.0)
                ),
                segment=row.segment,
            )
            for row in bucket
        ]
        forecast = forecast_cash(inputs, tables, windows=windows, draws=draws, seed=seed + index)
        realised = _realised_windows(bucket, windows=windows)
        bucket_results.append(
            BucketResult(
                bucket_start=bucket[0].flagged_date.isoformat(),
                n_invoices=len(bucket),
                realised={w: round(realised[w], 2) for w in windows},
                mean={w: forecast.windows[w].mean for w in windows},
                p5={w: forecast.windows[w].p5 for w in windows},
                p95={w: forecast.windows[w].p95 for w in windows},
            )
        )

    coverage: dict[int, float] = {}
    bias: dict[int, float] = {}
    mae: dict[int, float] = {}
    rmse: dict[int, float] = {}
    realised_total: dict[int, float] = {}
    forecast_mean_total: dict[int, float] = {}
    for window in windows:
        hits = sum(1 for b in bucket_results if b.p5[window] <= b.realised[window] <= b.p95[window])
        coverage[window] = hits / len(bucket_results)
        realised_total[window] = round(sum(b.realised[window] for b in bucket_results), 2)
        forecast_mean_total[window] = round(sum(b.mean[window] for b in bucket_results), 2)
        denom = realised_total[window] if realised_total[window] > 0 else 1.0
        bias[window] = (forecast_mean_total[window] - realised_total[window]) / denom
        errors = [b.mean[window] - b.realised[window] for b in bucket_results]
        mae[window] = float(np.mean(np.abs(errors)))
        rmse[window] = float(np.sqrt(np.mean(np.square(errors))))

    return BacktestReport(
        windows=windows,
        buckets=tuple(bucket_results),
        coverage=coverage,
        bias=bias,
        mae=mae,
        rmse=rmse,
        realised_total=realised_total,
        forecast_mean_total=forecast_mean_total,
    )
