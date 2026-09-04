"""Drift integration: serve-side aggregation, nightly alert copy, and the API.

The aggregation test pins exact feature values for a hand-built two-invoice
customer over three 30-day windows ending 2026-09-04:

* windows: [06-07..07-06], [07-07..08-05], [08-06..09-04]
* invoice A: billed 3000 in window 0, settled in window 1 ten days late
* invoice B: billed 1000 in window 2, still open and 15 days overdue

The API tests assert the perimeter (bearer-locked flags, public card) and
shapes, never live model output.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.services.drift import InvoiceFacts, customer_series
from scripts.run_drift_detection import render_alert


def _facts() -> list[InvoiceFacts]:
    return [
        InvoiceFacts(
            amount=3000.0,
            amount_paid=3000.0,
            issue_date=date(2026, 6, 10),
            due_date=date(2026, 7, 10),
            paid_at=datetime(2026, 7, 20, 12, 0),
        ),
        InvoiceFacts(
            amount=1000.0,
            amount_paid=0.0,
            issue_date=date(2026, 8, 10),
            due_date=date(2026, 8, 20),
            paid_at=None,
        ),
    ]


def test_customer_series_matches_hand_computed_windows():
    from src.ml.drift.features import from_period_series

    series = customer_series(_facts(), lifetime_scale=12000.0, as_of=date(2026, 9, 4))

    assert series.paid == (0.0, 3000.0, 0.0)
    assert series.billed == (3000.0, 0.0, 1000.0)
    assert series.delays_months[0] == pytest.approx(0.0)
    assert series.delays_months[1] == pytest.approx(10 / 30)
    assert series.delays_months[2] == pytest.approx(15 / 30)

    features = from_period_series(series)
    assert features["payment_delay_mean"] == pytest.approx((0 + 10 / 30 + 15 / 30) / 3)
    assert features["payment_delay_max"] == pytest.approx(0.5)
    assert features["payment_delay_trend"] == pytest.approx(0.25)
    assert features["delinquent_rate"] == pytest.approx(2 / 3)
    assert features["pay_ratio_mean"] == pytest.approx(1 / 3)
    assert features["missed_rate"] == pytest.approx(2 / 3)
    assert features["utilization"] == pytest.approx(4000 / 3 / 12000)


def test_empty_book_scores_as_pristine():
    from src.ml.drift.features import from_period_series

    series = customer_series([], lifetime_scale=5000.0, as_of=date(2026, 9, 4))
    features = from_period_series(series)

    assert features["payment_delay_max"] == 0.0
    assert features["missed_rate"] == 0.0
    assert features["pay_ratio_mean"] == pytest.approx(1.0)


def test_render_alert_names_every_flagged_customer():
    subject, body = render_alert(
        [
            ("C-1", "Acme", -0.05, [("payment_delay_max", 12.0)]),
            ("C-2", "Globex", -0.02, []),
        ],
        model_version="iforest-drift-test",
        window_days=90,
    )

    assert "2 customer" in subject
    assert "C-1" in body and "Acme" in body
    assert "C-2" in body
    assert "payment_delay_max" in body
    assert "/api/v1/drift/flags" in body


def test_render_alert_says_so_when_nobody_drifted():
    subject, body = render_alert([], model_version="v", window_days=90)

    assert "0 customer" in subject
    assert "No customers drifted" in body


# --- API -----------------------------------------------------------------------


@pytest.fixture
async def async_client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def auth_headers(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "the-cron-secret")
    monkeypatch.delenv("API_KEY", raising=False)
    yield {"Authorization": "Bearer the-cron-secret"}
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_drift_card_is_public_and_shaped(async_client: AsyncClient):
    response = await async_client.get("/api/v1/models/drift/card")

    assert response.status_code == 200
    data = response.json()
    assert data["shipped_model"] == "iforest-drift"
    assert data["source"] in {"model_card.json", "evaluation_report.json", "model_card_fallback"}
    assert isinstance(data["results"], list) and data["results"]
    assert data["injected_drift"]["recall"] > 0.5
    assert isinstance(data["limitations"], list) and data["limitations"]


@pytest.mark.asyncio
async def test_flags_without_a_key_are_refused(async_client: AsyncClient):
    for method, path in [
        ("GET", "/api/v1/drift/flags"),
        ("GET", "/api/v1/drift/flags/C-1"),
    ]:
        response = await async_client.request(method, path)
        assert response.status_code in (401, 503)


@pytest.mark.asyncio
async def test_flags_list_shape_with_a_key(async_client: AsyncClient, auth_headers: dict):
    response = await async_client.get("/api/v1/drift/flags", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 0
    for item in data["items"]:
        assert item["anomaly_score"] < item["threshold"] or not item["flagged"]
        assert item["model_version"]


@pytest.mark.asyncio
async def test_unknown_customer_flag_is_404(async_client: AsyncClient, auth_headers: dict):
    response = await async_client.get(
        "/api/v1/drift/flags/CUST-DOES-NOT-EXIST", headers=auth_headers
    )

    assert response.status_code == 404
