"""Tests for SDV GaussianCopulaSynthesizer integration and SDMetrics evaluation."""

from pathlib import Path

import pytest

pytest.importorskip("sdmetrics")
pytest.importorskip("sdv")

from src.data.sdv_generator import (
    RecoupSDVSynthesizer,
    batch_to_dataframe,
    build_sdv_metadata,
)
from src.data.synthetic_generator import generate_batch


@pytest.fixture(scope="module")
def sample_batch():
    return generate_batch(batch_size=40, customer_count=15, seed=42)


def test_batch_to_dataframe(sample_batch):
    df = batch_to_dataframe(sample_batch)
    assert len(df) == len(sample_batch.invoices)
    assert "invoice_id" in df.columns
    assert "customer_id" in df.columns
    assert "amount" in df.columns
    assert "days_overdue" in df.columns
    assert "on_time_ratio_all_time" in df.columns
    assert "recovered" in df.columns
    assert (df["amount"] > 0).all()


def test_sdv_metadata_construction(sample_batch):
    df = batch_to_dataframe(sample_batch)
    metadata = build_sdv_metadata(df)
    meta_dict = metadata.to_dict()
    assert "columns" in meta_dict
    assert meta_dict["primary_key"] == "invoice_id"
    assert meta_dict["columns"]["amount"]["sdtype"] == "numerical"
    assert meta_dict["columns"]["recovered"]["sdtype"] == "boolean"


def test_sdv_fit_sample_and_bounds(sample_batch):
    synthesizer = RecoupSDVSynthesizer()
    synthesizer.fit(sample_batch)

    synthetic_df = synthesizer.sample(num_rows=25)
    assert len(synthetic_df) == 25
    assert set(synthetic_df.columns) >= {
        "invoice_id",
        "customer_id",
        "amount",
        "days_overdue",
        "recovered",
        "industry",
    }
    # Check domain bounds
    assert (synthetic_df["amount"] >= 1000.0).all()
    assert (synthetic_df["days_overdue"] >= 0).all()
    assert (synthetic_df["on_time_ratio_90d"] >= 0.0).all()
    assert (synthetic_df["on_time_ratio_90d"] <= 1.0).all()


def test_sdv_sample_to_batch(sample_batch):
    synthesizer = RecoupSDVSynthesizer()
    synthesizer.fit(sample_batch)

    batch = synthesizer.sample_to_batch(num_rows=20)
    assert len(batch.invoices) == 20
    assert len(batch.customers) > 0
    assert batch.generator_version == "sdv-gaussian-copula-v1"

    inv = batch.invoices[0]
    assert inv.invoice_id.startswith("INV-SDV-")
    assert inv.amount >= 1000.0
    assert inv.due_date <= inv.flagged_date


def test_sdmetrics_quality_evaluation(sample_batch):
    df = batch_to_dataframe(sample_batch)
    synthesizer = RecoupSDVSynthesizer()
    synthesizer.fit(df)

    synthetic_df = synthesizer.sample(num_rows=40)
    quality = synthesizer.evaluate_quality(df, synthetic_df)

    assert "quality_score" in quality
    assert "column_shapes_score" in quality
    assert "column_pair_trends_score" in quality
    assert 0.0 <= quality["quality_score"] <= 1.0
    assert quality["quality_score"] > 0.6  # High fidelity threshold


def test_sdv_save_and_load(sample_batch, tmp_path: Path):
    model_path = tmp_path / "test_sdv_model.pkl"
    synthesizer = RecoupSDVSynthesizer()
    synthesizer.fit(sample_batch)
    synthesizer.save(model_path)
    assert model_path.exists()

    loaded = RecoupSDVSynthesizer.load(model_path)
    sampled = loaded.sample(num_rows=10)
    assert len(sampled) == 10
