"""Drift features, shared by training and serving.

Train/serve parity is the point of this module: the training script builds
these columns from UCI monthly histories through ``from_period_series``,
and the backend builds them from trailing-window aggregates through the
* same* function. One implementation, imported by both sides, so the two
can never drift apart silently.

Unit convention: delays are measured in **months of 30 days**. The UCI
source already reports repayment delay in months; the backend divides its
day-counts by 30 before calling in. Every other column is a rate, ratio,
or moment, which makes the whole schema invariant to window length --
training uses six monthly periods, serving uses three trailing 30-day
periods, and a rate means the same thing under both.

The nine columns:

* ``payment_delay_mean`` / ``payment_delay_max`` -- level of lateness.
* ``payment_delay_trend`` -- OLS slope of delay across periods; the
  degradation signal. Positive means getting worse.
* ``delinquent_rate`` -- fraction of periods with any delay.
* ``pay_ratio_mean`` -- mean paid/billed coverage, clipped to [0, 1].
* ``pay_ratio_trend`` -- slope of coverage; negative means paying less.
* ``bill_volatility`` -- coefficient of variation of billed amounts.
* ``utilization`` -- billed relative to credit limit (serve-side: relative
  to recent billing scale; see the model card for the adaptation).
* ``missed_rate`` -- fraction of billed periods with zero payment.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FEATURE_COLUMNS: tuple[str, ...] = (
    "payment_delay_mean",
    "payment_delay_max",
    "payment_delay_trend",
    "delinquent_rate",
    "pay_ratio_mean",
    "pay_ratio_trend",
    "bill_volatility",
    "utilization",
    "missed_rate",
)

FEATURE_SET_VERSION = "drift-v1"

#: Days per month in the delay unit convention. Backend day-counts are
#: divided by this before scoring; UCI months pass through unchanged.
DAYS_PER_MONTH = 30.0


@dataclass(frozen=True)
class PeriodSeries:
    """One customer's trailing behavior as parallel per-period series.

    ``delays_months`` holds repayment delay per period in months (>= 0).
    ``paid`` / ``billed`` hold amounts in a single currency. ``limit`` is
    the credit limit (training) or the serve-side billing scale proxy.
    All three series must have the same non-zero length.
    """

    delays_months: tuple[float, ...]
    paid: tuple[float, ...]
    billed: tuple[float, ...]
    limit: float


def _slope(values: np.ndarray) -> float:
    """OLS slope of a series against period index 0..n-1. Zero for n < 2."""

    n = values.shape[0]
    if n < 2:
        return 0.0
    x = np.arange(n, dtype="float64")
    x = x - x.mean()
    denom = float(np.dot(x, x))
    if denom == 0.0:
        return 0.0
    return float(np.dot(x, values - values.mean()) / denom)


def from_period_series(series: PeriodSeries) -> dict[str, float]:
    """Build the nine drift features from one customer's period series."""

    delays = np.asarray(series.delays_months, dtype="float64")
    paid = np.asarray(series.paid, dtype="float64")
    billed = np.asarray(series.billed, dtype="float64")
    if not (delays.shape == paid.shape == billed.shape and delays.shape[0] > 0):
        raise ValueError("delays, paid and billed must be non-empty and equal length")

    delays = np.clip(delays, 0.0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        coverage = np.where(billed > 0.0, np.clip(paid / billed, 0.0, 1.0), 1.0)

    n = delays.shape[0]
    billed_total = float(billed.sum())
    limit = max(float(series.limit), 1e-9)

    bill_mean = float(billed.mean())
    bill_std = float(billed.std())
    volatility = float(bill_std / bill_mean) if bill_mean > 0.0 else 0.0

    return {
        "payment_delay_mean": float(delays.mean()),
        "payment_delay_max": float(delays.max()),
        "payment_delay_trend": _slope(delays),
        "delinquent_rate": float(np.mean(delays > 0.0)),
        "pay_ratio_mean": float(coverage.mean()),
        "pay_ratio_trend": _slope(coverage),
        "bill_volatility": volatility,
        "utilization": float(np.clip(billed_total / n / limit, 0.0, 2.0)),
        "missed_rate": float(np.mean((billed > 0.0) & (paid <= 0.0))),
    }


def to_row(features: dict[str, float]) -> list[float]:
    """Order a feature dict into the canonical model input row."""

    try:
        return [float(features[column]) for column in FEATURE_COLUMNS]
    except KeyError as exc:
        raise ValueError(f"missing drift feature column: {exc}") from exc


def days_to_months(days: float) -> float:
    """Convert a backend day-count into the feature-space month unit."""
    return max(float(days), 0.0) / DAYS_PER_MONTH
