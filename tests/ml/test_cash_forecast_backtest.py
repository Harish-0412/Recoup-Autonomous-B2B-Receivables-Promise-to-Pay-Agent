"""Backtest, Platt calibration, and artifact round-trips.

The perfect-information test is the load-bearing one: when probabilities
equal realised labels and lags equal realised lags, the forecast mean must
track realised totals (bias ~ 0) and the 90% interval must cover nearly every
bucket. If that fails, the simulation math itself is wrong -- no amount of
model quality can save it.
"""

from datetime import timedelta

from src.ml.cash_forecast.artifacts import (
    CashForecastModel,
    CashForecastPayload,
    load_cash_forecast_model,
    payload_from_model,
    save_cash_forecast_model,
)
from src.ml.cash_forecast.backtest import fit_platt_scaler, run_backtest
from src.ml.cash_forecast.dataset import LagObservation
from src.ml.cash_forecast.lags import LagTables, fit_lag_tables
from src.ml.config import MLSettings
from tests.conftest import AS_OF


def _rows(n: int = 60, lag: int = 5, recovered_every: int = 2) -> list[LagObservation]:
    rows = []
    for i in range(n):
        recovered = i % recovered_every == 0
        rows.append(
            LagObservation(
                invoice_id=f"INV-{i:04d}",
                segment="mixed_slow_clean",
                lag_days=lag if recovered else -1,
                amount=50_000.0,
                flagged_date=AS_OF - timedelta(days=n - i),
                recovered=recovered,
            )
        )
    return rows


def _tables(rows: list[LagObservation]) -> LagTables:
    return fit_lag_tables(
        [(r.segment, r.lag_days) for r in rows if r.recovered],
        min_segment_samples=5,
        horizon_days=30,
    )


def test_perfect_information_backtest_is_unbiased_and_covered():
    rows = _rows()
    tables = _tables(rows)
    probs = {r.invoice_id: 1.0 if r.recovered else 0.0 for r in rows}
    report = run_backtest(rows, probs, tables, draws=5_000, seed=7)

    assert set(report.coverage) == {7, 30}
    assert report.coverage[30] >= 0.9
    assert abs(report.bias[30]) <= 0.05
    assert report.gates()["passed"] is True


def test_backtest_metrics_are_sane():
    rows = _rows()
    tables = _tables(rows)
    probs = {r.invoice_id: 0.5 for r in rows}
    report = run_backtest(rows, probs, tables, draws=2_000, seed=7)

    for window in (7, 30):
        assert 0.0 <= report.coverage[window] <= 1.0
        assert report.mae[window] >= 0.0
        assert report.rmse[window] >= report.mae[window] - 1e-9
        assert report.realised_total[window] >= 0.0
    assert len(report.buckets) >= 2


def test_backtest_needs_rows():
    tables = LagTables(segments={"s": (1, 2)}, global_lags=(1, 2))
    try:
        run_backtest([], {}, tables)
    except ValueError as exc:
        assert "at least one observation" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_platt_scaler_pulls_overconfident_probs_toward_base_rate():
    # 20% realised, model says 80% everywhere: calibration must pull down.
    probs = [0.8] * 100
    labels = [1] * 20 + [0] * 80
    scaler = fit_platt_scaler(probs, labels)
    assert scaler.calibrate(0.8) < 0.5
    # Identity-ish input stays identity-ish when already calibrated.
    even = fit_platt_scaler([0.5] * 60, [1] * 30 + [0] * 30)
    assert abs(even.calibrate(0.5) - 0.5) < 0.15


def test_platt_scaler_needs_pairs():
    try:
        fit_platt_scaler([], [])
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_artifact_round_trip_through_tmp_store(tmp_path):
    from src.ml.cash_forecast.backtest import PlattScaler

    settings = MLSettings(ml_artifacts_dir=tmp_path / "artifacts")
    model = CashForecastModel(
        lags=LagTables(
            segments={"mixed_slow_clean": (2, 4, 6)},
            global_lags=(2, 4, 6, 20),
            horizon_days=30,
            train_rows=4,
        ),
        scaler=PlattScaler(slope=0.9, intercept=0.05),
        scaler_fitted_rows=120,
    )
    _, metadata = save_cash_forecast_model(
        model, train_rows=4, metrics={"coverage_30d": 1.0}, settings=settings
    )
    loaded, loaded_meta = load_cash_forecast_model(settings=settings)

    assert loaded_meta == metadata
    assert loaded.lags.global_lags == (2, 4, 6, 20)
    assert loaded.scaler.slope == 0.9
    assert loaded.scaler_fitted_rows == 120


def test_payload_rejects_empty_global_lags():
    payload = CashForecastPayload(global_lags=[])
    try:
        payload.to_model()
    except ValueError as exc:
        assert "no global lags" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_payload_round_trip_preserves_pools():
    from src.ml.cash_forecast.backtest import PlattScaler

    model = CashForecastModel(
        lags=LagTables(
            segments={"a": (1, 2)},
            global_lags=(1, 2, 9),
            pooled_segments=("b",),
        ),
        scaler=PlattScaler(slope=1.0, intercept=0.0),
    )
    revived = CashForecastPayload(**vars(payload_from_model(model))).to_model()
    assert revived == model
    assert isinstance(revived.lags.segments["a"], tuple)
