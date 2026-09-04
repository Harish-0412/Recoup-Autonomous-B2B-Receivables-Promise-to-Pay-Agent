"""Monte Carlo cash forecast: probability x timing, aggregated by window.

For each at-risk invoice the model takes two independent statements --
"P(pays within the horizon) = p" from the recovery scorer and "given that it
pays, the day it lands on is drawn from this segment's empirical lag table" --
and simulates ``draws`` futures. Each future pays out per invoice with
probability p on its drawn lag day; windowed totals are the sum of payouts
landing inside each window. Percentiles over the futures are the forecast.

Vectorised per lag-pool (segment or global): invoices sharing a pool draw
from one matrix, so 10k draws over a few hundred invoices is milliseconds of
numpy, not a Python loop of millions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.ml.cash_forecast.lags import LagTables

#: Forecast windows in days, matching the recovery model's 30-day horizon.
WINDOWS: tuple[int, ...] = (7, 30)

MIN_DRAWS = 100
MAX_DRAWS = 50_000
DEFAULT_DRAWS = 10_000


@dataclass(frozen=True)
class ForecastInput:
    """One at-risk invoice entering the simulation."""

    invoice_id: str
    amount: float
    p_recovery: float
    segment: str


@dataclass(frozen=True)
class WindowStats:
    """The simulated distribution of cash landing inside one window."""

    window_days: int
    draws: int
    mean: float
    median: float
    p5: float
    p25: float
    p75: float
    p95: float
    prob_any_cash: float


@dataclass(frozen=True)
class CashForecast:
    """Windowed forecasts plus the provenance to reproduce them."""

    windows: dict[int, WindowStats]
    n_invoices: int
    at_risk_value: float
    draws: int
    seed: int


def _summarise(window_days: int, totals: np.ndarray, draws: int) -> WindowStats:
    percentiles = np.percentile(totals, [5, 25, 50, 75, 95])
    return WindowStats(
        window_days=window_days,
        draws=draws,
        mean=round(float(np.mean(totals)), 2),
        median=round(float(percentiles[2]), 2),
        p5=round(float(percentiles[0]), 2),
        p25=round(float(percentiles[1]), 2),
        p75=round(float(percentiles[3]), 2),
        p95=round(float(percentiles[4]), 2),
        prob_any_cash=round(float(np.mean(totals > 0.0)), 4),
    )


def forecast_cash(
    inputs: list[ForecastInput],
    tables: LagTables,
    *,
    windows: tuple[int, ...] = WINDOWS,
    draws: int = DEFAULT_DRAWS,
    seed: int = 0,
) -> CashForecast:
    """Simulate windowed cash recovery over the at-risk book.

    Deterministic for a fixed seed: the same inputs, tables, draws and seed
    produce bit-identical percentiles, which is what makes the forecast
    testable and the API response reproducible.
    """

    if not MIN_DRAWS <= draws <= MAX_DRAWS:
        raise ValueError(f"draws must be in [{MIN_DRAWS}, {MAX_DRAWS}]")
    if any(w <= 0 for w in windows):
        raise ValueError("windows must be positive day counts")
    if any(i.amount < 0 for i in inputs):
        raise ValueError("invoice amounts must be non-negative")

    at_risk = round(float(sum(i.amount for i in inputs)), 2)
    if not inputs:
        return CashForecast(
            windows={w: WindowStats(w, draws, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0) for w in windows},
            n_invoices=0,
            at_risk_value=at_risk,
            draws=draws,
            seed=seed,
        )

    rng = np.random.default_rng(seed)
    totals: dict[int, np.ndarray] = {w: np.zeros(draws) for w in windows}

    # Invoices sharing one lag pool simulate as one matrix.
    pools: dict[str, list[int]] = {}
    for idx, item in enumerate(inputs):
        pool_key = item.segment if item.segment in tables.segments else "__global__"
        pools.setdefault(pool_key, []).append(idx)

    for pool_key, indices in pools.items():
        pool_lags = tables.segments[pool_key] if pool_key != "__global__" else tables.global_lags
        if not pool_lags:
            raise ValueError("lag tables are empty: fit on realised lags before forecasting")
        amounts = np.array([inputs[i].amount for i in indices])
        probs = np.clip([inputs[i].p_recovery for i in indices], 0.0, 1.0)
        lag_choices = np.array(pool_lags)

        pays = rng.random((draws, len(indices))) < probs[None, :]
        lag_idx = rng.integers(0, len(lag_choices), size=(draws, len(indices)))
        lag_days = lag_choices[lag_idx]
        paid = pays * amounts[None, :]  # (draws, k): rupees landing per invoice

        for window in windows:
            in_window = (lag_days <= window) & pays
            totals[window] += (paid * in_window).sum(axis=1)

    return CashForecast(
        windows={w: _summarise(w, totals[w], draws) for w in windows},
        n_invoices=len(inputs),
        at_risk_value=at_risk,
        draws=draws,
        seed=seed,
    )
