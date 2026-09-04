"""Tests for GET /forecast/cash and GET /forecast/cash/card.

No database: ``get_db`` is overridden with a dummy session, the repository
lookups and the model loader are monkeypatched. What is asserted is routing,
the 503-without-artifact contract, percentile ordering on a stubbed book,
and the card fallback -- never collection behaviour.
"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import cash_forecast
from app.core.security import require_api_key
from app.db.session import get_db
from app.main import app
from src.ml.cash_forecast.artifacts import CashForecastModel
from src.ml.cash_forecast.backtest import PlattScaler
from src.ml.cash_forecast.lags import LagTables
from src.ml.schemas import ModelMetadata
from src.ml.versioning import utc_now


def _case(invoice_id="INV-1", amount=100_000.0):
    return SimpleNamespace(
        invoice=SimpleNamespace(invoice_id=invoice_id, outstanding=amount),
        customer=SimpleNamespace(
            on_time_ratio_90d=0.9,
            avg_days_late=2.0,
            broken_promise_rate=0.0,
            dispute_rate=0.0,
        ),
    )


def _model():
    return CashForecastModel(
        lags=LagTables(
            segments={"reliable_prompt_clean": (1, 2, 3, 5, 8)},
            global_lags=(1, 2, 3, 5, 8, 13),
            horizon_days=30,
            train_rows=6,
        ),
        scaler=PlattScaler(slope=1.0, intercept=0.0),
    )


def _metadata():
    return ModelMetadata(
        model_name="cash-forecast-model",
        model_version="cash-forecast-model-test",
        trained_at=utc_now(),
        train_rows=6,
        feature_columns=["amount", "p_recovery", "segment"],
        metrics={},
    )


@pytest.fixture
def stubbed_app(monkeypatch):
    async def _no_db():
        yield None

    async def _invoices(session, business_id, limit=500):
        return [SimpleNamespace(invoice_id=f"INV-{i}") for i in range(5)]

    async def _case_row(session, invoice, business_id):
        return _case(invoice_id=invoice.invoice_id)

    from app.core.tenancy import TenantContext, require_tenant
    from app.services import repository

    cash_forecast.clear_forecast_cache()
    monkeypatch.setattr(repository, "list_open_invoices", _invoices)
    monkeypatch.setattr(repository, "load_case", _case_row)
    monkeypatch.setattr(cash_forecast, "load_forecast_model", lambda: (_model(), _metadata()))
    monkeypatch.setattr(
        cash_forecast,
        "score_book_probabilities",
        lambda cases: ([0.8] * len(cases), "test-scorer", 0),
    )
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_tenant] = lambda: TenantContext(business_id="default")
    cash_forecast.clear_forecast_cache()
    yield app
    app.dependency_overrides.clear()
    cash_forecast.clear_forecast_cache()


@pytest.fixture
async def async_client(stubbed_app):
    transport = ASGITransport(app=stubbed_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_cash_forecast_returns_ordered_windows(async_client: AsyncClient):
    response = await async_client.get("/api/v1/forecast/cash", params={"draws": 1000, "seed": 4})
    assert response.status_code == 200
    payload = response.json()
    assert payload["n_invoices"] == 5
    assert payload["at_risk_value"] == pytest.approx(500_000.0)
    assert payload["lag_model_version"] == "cash-forecast-model-test"
    windows = {w["window_days"]: w for w in payload["windows"]}
    assert set(windows) == {7, 30}
    for stats in windows.values():
        assert 0.0 <= stats["p5"] <= stats["median"] <= stats["p95"]
        assert stats["mean"] >= 0.0
    assert windows[7]["mean"] <= windows[30]["mean"]


@pytest.mark.asyncio
async def test_cash_forecast_without_artifact_is_503(async_client: AsyncClient, monkeypatch):
    from app.api.cash_forecast import ForecastUnavailable

    def _missing():
        raise ForecastUnavailable("No trained cash-forecast artifact.")

    monkeypatch.setattr(cash_forecast, "load_forecast_model", _missing)
    response = await async_client.get("/api/v1/forecast/cash")
    assert response.status_code == 503
    assert (
        "train_cash_forecast" in response.json()["detail"]
        or "artifact" in response.json()["detail"]
    )


@pytest.mark.asyncio
async def test_cash_forecast_rejects_draws_outside_bounds(async_client: AsyncClient):
    response = await async_client.get("/api/v1/forecast/cash", params={"draws": 10})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cash_card_reports_source(async_client: AsyncClient):
    response = await async_client.get("/api/v1/forecast/cash/card")
    assert response.status_code == 200
    assert "source" in response.json()
