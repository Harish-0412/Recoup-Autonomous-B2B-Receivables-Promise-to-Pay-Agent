"""Tests for POST /policy/simulate.

The engine does not exist yet, so these tests pin the *contract*: a valid
what-if body reaches the stub and gets an honest 501, while an invalid body
is rejected by validation (422) before any engine could run. When the engine
ships, the 501 assertions become 200 assertions against the same shapes.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _body(**overrides):
    payload = {
        "policy_overrides": {"discount_ceiling_pct": 15.0},
        "replay_window": {"from": "2026-06-01", "to": "2026-09-01"},
    }
    payload["policy_overrides"].update(overrides)
    return payload


@pytest.mark.asyncio
async def test_simulate_valid_body_gets_honest_501(async_client: AsyncClient):
    response = await async_client.post("/api/v1/policy/simulate", json=_body())
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["status"] == "not_implemented"
    assert "architecture.md" in detail["reason"]


@pytest.mark.asyncio
async def test_simulate_rejects_out_of_range_ceiling(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/policy/simulate", json=_body(discount_ceiling_pct=150.0)
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_simulate_requires_replay_window(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/policy/simulate", json={"policy_overrides": {}}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_simulate_contract_shapes_are_importable():
    from app.schemas.policy import PolicySimulateIn, PolicySimulateOut

    incoming = PolicySimulateIn.model_validate(_body(max_contacts_per_invoice=6))
    assert incoming.policy_overrides.max_contacts_per_invoice == 6
    assert incoming.replay_window.from_date.isoformat() == "2026-06-01"

    # The engine's future answer validates against the outgoing schema today.
    outgoing = PolicySimulateOut(
        baseline={
            "recovery_rate": 0.635,
            "recovered_value": 39700000.0,
            "false_interventions": 263,
            "compliance_violations": 0,
        },
        simulated={
            "recovery_rate": 0.641,
            "recovered_value": 40100000.0,
            "false_interventions": 281,
            "compliance_violations": 0,
        },
        delta={
            "recovery_rate": 0.006,
            "recovered_value": 400000.0,
            "false_interventions": 18,
            "compliance_violations": 0,
        },
        cases_affected=[],
        cases_replayed=600,
    )
    assert outgoing.delta.recovered_value == 400000.0
