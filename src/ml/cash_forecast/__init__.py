"""Receivables-specific cash forecast: which rupees land, and when.

Pipeline: per-invoice recovery probability (from the recovery scorer) x an
empirical payment-lag distribution per observable customer segment, combined
by Monte Carlo into windowed (7-day / 30-day) cash distributions.

The lag tables are fitted on *realized* payment lags only -- invoices actually
observed paid, with the lag measured flag-date to paid-date. Invoices whose
payment would have landed past the observation horizon are right-censored and
excluded from the lag fit (their mass lives in the recovery probability, not
in the lag table). Segments use observable behaviour only; the generator's
hidden archetype never reaches this package.
"""

from src.ml.cash_forecast.lags import LagTables, fit_lag_tables, lags_for, segment_for_case
from src.ml.cash_forecast.simulate import ForecastInput, WindowStats, forecast_cash

__all__ = [
    "ForecastInput",
    "LagTables",
    "WindowStats",
    "fit_lag_tables",
    "forecast_cash",
    "lags_for",
    "segment_for_case",
]
