"""Unit tests for the drift package: features, model, service, evaluation.

Everything here is hermetic except the two UCI tests, which read the cached
source file and skip when it is absent (a fresh clone has no data by
design). No test touches the network: the loader only downloads when the
cache is missing, and these tests never let it get that far.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.ml.config import MLSettings
from src.ml.drift.dataset import (
    SOURCE_ROWS,
    TARGET_COL,
    build_feature_frame,
    degrade_series,
    load_uci_frame,
    row_to_series,
)
from src.ml.drift.features import (
    FEATURE_COLUMNS,
    PeriodSeries,
    days_to_months,
    from_period_series,
    to_row,
)
from src.ml.drift.models import fit_drift_model
from src.ml.drift.service import DriftScorer, top_drivers_for

DATA_DIR = Path("data/drift/raw")
needs_uci = pytest.mark.skipif(
    not (DATA_DIR / "default of credit card clients.xls").exists(),
    reason="UCI source not cached locally",
)


def _clean_series() -> PeriodSeries:
    return PeriodSeries(
        delays_months=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        paid=(1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0),
        billed=(1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0),
        limit=20000.0,
    )


# --- shared feature function -------------------------------------------------


def test_feature_schema_has_nine_documented_columns():
    assert len(FEATURE_COLUMNS) == 9
    assert "payment_delay_trend" in FEATURE_COLUMNS
    assert "dispute_count" not in FEATURE_COLUMNS


def test_clean_payer_has_zero_delay_and_full_coverage():
    features = from_period_series(_clean_series())

    assert features["payment_delay_mean"] == 0.0
    assert features["payment_delay_max"] == 0.0
    assert features["payment_delay_trend"] == 0.0
    assert features["delinquent_rate"] == 0.0
    assert features["pay_ratio_mean"] == pytest.approx(1.0)
    assert features["missed_rate"] == 0.0


def test_degrading_trend_is_positive_and_coverage_falls():
    series = PeriodSeries(
        delays_months=(0.0, 0.0, 1.0, 2.0, 3.0, 4.0),
        paid=(1000.0, 1000.0, 800.0, 400.0, 200.0, 0.0),
        billed=(1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0),
        limit=20000.0,
    )
    features = from_period_series(series)

    assert features["payment_delay_trend"] > 0.0
    assert features["pay_ratio_trend"] < 0.0
    assert features["delinquent_rate"] == pytest.approx(4 / 6)
    assert features["missed_rate"] == pytest.approx(1 / 6)


def test_mismatched_series_lengths_are_rejected():
    with pytest.raises(ValueError):
        from_period_series(
            PeriodSeries(delays_months=(0.0,), paid=(1.0, 2.0), billed=(1.0,), limit=1.0)
        )


def test_to_row_orders_and_validates_columns():
    features = from_period_series(_clean_series())
    row = to_row(features)

    assert len(row) == len(FEATURE_COLUMNS)
    with pytest.raises(ValueError):
        to_row({"payment_delay_mean": 0.0})


def test_days_to_months_converts_and_floors():
    assert days_to_months(30.0) == pytest.approx(1.0)
    assert days_to_months(-5.0) == 0.0


# --- model --------------------------------------------------------------------


def _normals_frame(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    delays = rng.choice([0.0, 0.0, 0.0, 1.0], size=(n, 6))
    paid = np.full((n, 6), 1000.0)
    billed = np.full((n, 6), 1000.0)
    rows = []
    for i in range(n):
        rows.append(
            from_period_series(
                PeriodSeries(
                    delays_months=tuple(float(v) for v in delays[i]),
                    paid=tuple(float(v) for v in paid[i]),
                    billed=tuple(float(v) for v in billed[i]),
                    limit=20000.0,
                )
            )
        )
    return pd.DataFrame(rows, columns=list(FEATURE_COLUMNS)).astype("float64")


def test_threshold_flags_about_contamination_of_train_normals():
    frame = _normals_frame()
    model = fit_drift_model(frame, contamination=0.10, seed=42)

    assert float(np.mean(model.is_flagged(frame))) == pytest.approx(0.10, abs=0.02)


def test_degraded_customer_scores_below_a_clean_one():
    frame = _normals_frame()
    model = fit_drift_model(frame, contamination=0.10, seed=42)

    clean = _clean_series()
    bad = PeriodSeries(
        delays_months=(0.0, 0.0, 1.0, 2.0, 3.0, 4.0),
        paid=(1000.0, 1000.0, 800.0, 400.0, 200.0, 0.0),
        billed=(1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0),
        limit=20000.0,
    )
    assert model.score_one(to_row_as_dict(bad)) < model.score_one(to_row_as_dict(clean))


def to_row_as_dict(series: PeriodSeries) -> dict[str, float]:
    from src.ml.drift.features import from_period_series as build

    return build(series)


def test_service_falls_back_without_an_artifact(tmp_path, monkeypatch):
    # NOTE: pass the directory via environment, not constructor kwarg --
    # MLSettings ignores field-name kwargs (pydantic-settings 2.6.1 quirk
    # this repo works around the same way in test_config.py).
    monkeypatch.setenv("ML_ARTIFACTS_DIR", str(tmp_path / "empty-store"))
    settings = MLSettings()
    scorer = DriftScorer(settings=settings)

    assert scorer.available is False
    result = scorer.score_series("C-1", _clean_series())

    assert result.fallback_used is True
    assert result.flagged is False
    assert result.anomaly_score is None
    assert result.top_drivers == ()


def test_top_drivers_rank_delay_first_for_a_degrading_customer():
    frame = _normals_frame()
    model = fit_drift_model(frame, contamination=0.10, seed=42)
    features = to_row_as_dict(
        PeriodSeries(
            delays_months=(0.0, 0.0, 1.0, 2.0, 3.0, 4.0),
            paid=(1000.0, 1000.0, 800.0, 400.0, 200.0, 0.0),
            billed=(1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0),
            limit=20000.0,
        )
    )

    drivers = top_drivers_for(model, features)

    assert len(drivers) == 3
    assert drivers[0].feature in {
        "payment_delay_max",
        "payment_delay_mean",
        "payment_delay_trend",
    }


# --- real data (cached source only) --------------------------------------------


@needs_uci
def test_uci_source_loads_with_expected_shape_and_target():
    frame = load_uci_frame(DATA_DIR)

    assert len(frame) == SOURCE_ROWS
    assert frame.isnull().sum().sum() == 0
    assert 0.20 < float(frame[TARGET_COL].mean()) < 0.25


@needs_uci
def test_uci_row_adapter_matches_shared_feature_function():
    frame = load_uci_frame(DATA_DIR)
    row = frame.iloc[0]

    via_adapter = from_period_series(row_to_series(row))
    direct = from_period_series(
        PeriodSeries(
            delays_months=tuple(
                max(float(row[c]), 0.0)
                for c in ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
            ),
            paid=tuple(max(float(row[c]), 0.0) for c in [f"PAY_AMT{i}" for i in range(1, 7)]),
            billed=tuple(max(float(row[c]), 0.0) for c in [f"BILL_AMT{i}" for i in range(1, 7)]),
            limit=max(float(row["LIMIT_BAL"]), 1e-9),
        )
    )

    assert via_adapter == direct
    assert set(via_adapter) == set(FEATURE_COLUMNS)


@needs_uci
def test_degrade_series_worsens_recent_periods_only():
    frame = load_uci_frame(DATA_DIR)
    series = row_to_series(frame.iloc[0])
    degraded = degrade_series(series, seed=1)

    assert degraded.billed == series.billed
    assert degraded.delays_months[-1] >= series.delays_months[-1]
    assert degraded.paid[-1] <= series.paid[-1]


@needs_uci
def test_feature_frame_covers_every_customer():
    frame = load_uci_frame(DATA_DIR).head(200)

    built = build_feature_frame(frame)

    assert list(built.columns) == list(FEATURE_COLUMNS)
    assert len(built) == 200
    assert built["delinquent_rate"].between(0.0, 1.0).all()


def test_invoice_facts_dataclass_shape():
    from app.services.drift import InvoiceFacts

    facts = InvoiceFacts(
        amount=1000.0,
        amount_paid=0.0,
        issue_date=date(2026, 8, 10),
        due_date=date(2026, 8, 20),
        paid_at=None,
    )
    assert facts.paid_at is None
    assert isinstance(facts.due_date, date)
    assert datetime(2026, 8, 10).date() == facts.issue_date
