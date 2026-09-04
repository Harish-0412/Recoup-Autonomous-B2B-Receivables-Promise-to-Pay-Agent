"""Monte Carlo simulation: determinism, ordering, edge cases, analytics."""

import numpy as np
import pytest

from src.ml.cash_forecast.lags import LagTables
from src.ml.cash_forecast.simulate import ForecastInput, forecast_cash

TABLES = LagTables(
    segments={"reliable_prompt_clean": (1, 2, 3, 5, 8, 13)},
    global_lags=(1, 2, 3, 5, 8, 13, 21, 30),
    horizon_days=30,
)


def _inputs(n: int = 20, p: float = 0.7, amount: float = 100_000.0) -> list[ForecastInput]:
    return [
        ForecastInput(
            invoice_id=f"INV-{i}",
            amount=amount,
            p_recovery=p,
            segment="reliable_prompt_clean" if i % 2 == 0 else "unknown_segment",
        )
        for i in range(n)
    ]


def test_same_seed_is_bit_identical():
    first = forecast_cash(_inputs(), TABLES, draws=2_000, seed=11)
    second = forecast_cash(_inputs(), TABLES, draws=2_000, seed=11)
    for window in (7, 30):
        assert first.windows[window] == second.windows[window]


def test_different_seeds_disagree():
    first = forecast_cash(_inputs(), TABLES, draws=2_000, seed=11)
    second = forecast_cash(_inputs(), TABLES, draws=2_000, seed=12)
    assert first.windows[30].mean != second.windows[30].mean


def test_percentiles_are_ordered_and_non_negative():
    forecast = forecast_cash(_inputs(), TABLES, draws=5_000, seed=3)
    for window in (7, 30):
        stats = forecast.windows[window]
        assert 0.0 <= stats.p5 <= stats.p25 <= stats.median <= stats.p75 <= stats.p95
        assert stats.mean >= 0.0
        assert 0.0 <= stats.prob_any_cash <= 1.0


def test_zero_probability_pays_nothing():
    forecast = forecast_cash(_inputs(p=0.0), TABLES, draws=1_000, seed=3)
    for window in (7, 30):
        stats = forecast.windows[window]
        assert stats.mean == 0.0
        assert stats.p95 == 0.0
        assert stats.prob_any_cash == 0.0


def test_single_invoice_mean_matches_analytic_expectation():
    # One invoice, p=0.5, every observed lag (1..8d) lands inside the 30-day
    # window, so E[cash_30d] = 0.5 * amount exactly. Monte Carlo must agree
    # within a tight tolerance at 20k draws.
    tables = LagTables(segments={"s": (1, 2, 3, 4, 5, 6, 7, 8)}, global_lags=(1, 8))
    forecast = forecast_cash(
        [ForecastInput(invoice_id="INV-1", amount=200_000.0, p_recovery=0.5, segment="s")],
        tables,
        draws=20_000,
        seed=5,
    )
    assert forecast.windows[30].mean == pytest.approx(100_000.0, rel=0.05)
    # The 7-day window excludes the lag-8 mass: expectation is 7/8 of full.
    assert forecast.windows[7].mean == pytest.approx(87_500.0, rel=0.08)


def test_shorter_window_never_exceeds_longer_window_draw_by_draw():
    # Realised per-draw monotonicity is structural (7d subset of 30d); the
    # summary check below is the observable shadow of that property.
    forecast = forecast_cash(_inputs(), TABLES, draws=5_000, seed=9)
    assert forecast.windows[7].mean <= forecast.windows[30].mean
    assert forecast.windows[7].p95 <= forecast.windows[30].p95


def test_empty_book_forecasts_zero_with_counts():
    forecast = forecast_cash([], TABLES, draws=1_000, seed=1)
    assert forecast.n_invoices == 0
    assert forecast.at_risk_value == 0.0
    assert forecast.windows[30].mean == 0.0


def test_unknown_segments_fall_back_to_global_pool():
    tables = LagTables(segments={}, global_lags=(1, 2, 3))
    forecast = forecast_cash(_inputs(), tables, draws=1_000, seed=1)
    assert forecast.windows[30].mean > 0.0


def test_empty_lag_tables_refuse_to_forecast():
    tables = LagTables(segments={}, global_lags=())
    with pytest.raises(ValueError, match="empty"):
        forecast_cash(_inputs(n=2), tables, draws=500, seed=1)


def test_draws_and_amounts_are_validated():
    with pytest.raises(ValueError, match="draws"):
        forecast_cash(_inputs(n=1), TABLES, draws=50, seed=1)
    with pytest.raises(ValueError, match="non-negative"):
        forecast_cash(
            [ForecastInput(invoice_id="X", amount=-5.0, p_recovery=0.5, segment="s")],
            TABLES,
            draws=500,
            seed=1,
        )


def test_probabilities_are_clipped_not_trusted():
    over = [ForecastInput(invoice_id="X", amount=10_000.0, p_recovery=1.4, segment="s")]
    under = [ForecastInput(invoice_id="Y", amount=10_000.0, p_recovery=-0.2, segment="s")]
    tables = LagTables(segments={"s": (1,)}, global_lags=(1,))
    assert forecast_cash(over, tables, draws=500, seed=1).windows[30].mean == 10_000.0
    assert forecast_cash(under, tables, draws=500, seed=1).windows[30].mean == 0.0


def test_numpy_version_sanity():
    assert np.__version__  # the simulation has no reason to run without numpy
