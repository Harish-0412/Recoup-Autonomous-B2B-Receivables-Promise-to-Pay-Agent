"""Lag tables: fitting, pooling discipline, and the observable-only segment."""

import pytest

from src.ml.cash_forecast.lags import fit_lag_tables, lags_for, segment_for_case
from tests.conftest import make_case


def test_segments_keep_enough_data_and_pool_the_rest():
    rows = [("reliable_prompt_clean", lag) for lag in (1, 2, 3, 4, 5)] + [
        ("strained_verylate_flagged", lag) for lag in (20, 25)
    ]
    tables = fit_lag_tables(rows, min_segment_samples=5, horizon_days=30)
    assert tables.segments["reliable_prompt_clean"] == (1, 2, 3, 4, 5)
    assert "strained_verylate_flagged" not in tables.segments
    assert tables.pooled_segments == ("strained_verylate_flagged",)
    # Pooled segments resolve to the global table, which holds every lag.
    assert lags_for(tables, "strained_verylate_flagged") == (1, 2, 3, 4, 5, 20, 25)


def test_unknown_segment_resolves_to_global():
    tables = fit_lag_tables([("a", 3), ("a", 4)], min_segment_samples=1)
    assert lags_for(tables, "never_seen") == (3, 4)


def test_lags_beyond_the_horizon_are_dropped_not_learned():
    tables = fit_lag_tables([("a", 5), ("a", 45), ("a", 60)], min_segment_samples=1)
    assert tables.segments["a"] == (5,)
    assert tables.global_lags == (5,)


def test_zero_or_negative_lags_clamp_to_one_day():
    tables = fit_lag_tables([("a", 0), ("a", -2)], min_segment_samples=1)
    assert tables.segments["a"] == (1, 1)


def test_empty_fit_refuses():
    with pytest.raises(ValueError, match="no observable lags"):
        fit_lag_tables([], min_segment_samples=5)
    with pytest.raises(ValueError, match="positive"):
        fit_lag_tables([("a", 3)], min_segment_samples=0)


def test_segment_uses_observables_only():
    # A reliable payer with no flags lands in the reliable/prompt/clean cell;
    # the mapping reads customer history, never the hidden archetype (which
    # CaseSnapshot cannot even carry).
    reliable = make_case(on_time_ratio=0.95, avg_days_late=1.0, broken_promises=0)
    assert segment_for_case(reliable) == "reliable_prompt_clean"

    strained = make_case(on_time_ratio=0.10, avg_days_late=40.0, broken_promises=5)
    assert segment_for_case(strained) == "strained_verylate_flagged"


def test_tables_are_sorted_for_inverse_cdf_sampling():
    tables = fit_lag_tables([("a", lag) for lag in (9, 1, 5, 1, 30)], min_segment_samples=1)
    lags = list(tables.segments["a"])
    assert lags == sorted(lags)
