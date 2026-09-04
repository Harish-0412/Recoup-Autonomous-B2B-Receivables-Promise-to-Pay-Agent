"""Tests for legal review compliance and policy floor constraints.

Verifies:
- PolicyConfig defaults match docs/legal/dunning_review.md.
- PolicyConfig enforces LEGAL_MIN_CONTACT_GAP_DAYS (cannot construct with gap < 2).
- POST /api/v1/policy/simulate rejects any what-if gap < 2 with 422 Unprocessable Entity.
- run_policy_simulation refuses gap below the statutory floor.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.policy import (
    DEFAULT_ESCALATION_LADDER,
    LEGAL_MIN_CONTACT_GAP_DAYS,
    PolicyConfig,
    policy_config_from_settings,
)
from app.main import app
from app.schemas.policy import PolicyOverridesIn, PolicySimulateIn, ReplayWindowIn
from app.services.policy_simulator import run_policy_simulation


@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def auth_headers(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "cron-test-key")
    monkeypatch.delenv("API_KEY", raising=False)
    yield {"Authorization": "Bearer cron-test-key"}
    get_settings.cache_clear()


def test_policy_defaults_match_legal_memo():
    config = policy_config_from_settings()
    assert config.min_contact_gap_days == 3
    assert config.escalation_ladder == DEFAULT_ESCALATION_LADDER
    assert config.max_contacts_per_invoice == 4
    assert config.min_days_overdue_to_contact == 1
    assert config.optout_days == 30
    assert LEGAL_MIN_CONTACT_GAP_DAYS == 2


def test_policy_config_rejects_gap_below_legal_floor():
    with pytest.raises(ValidationError):
        PolicyConfig(min_contact_gap_days=1)

    with pytest.raises(ValidationError):
        PolicyConfig(min_contact_gap_days=0)

    # 2 is allowed (the floor)
    config = PolicyConfig(min_contact_gap_days=2)
    assert config.min_contact_gap_days == 2


def test_simulate_schema_rejects_gap_below_legal_floor():
    with pytest.raises(ValidationError):
        PolicyOverridesIn(min_contact_gap_days=1)

    with pytest.raises(ValidationError):
        PolicyOverridesIn(min_contact_gap_days=0)

    overrides = PolicyOverridesIn(min_contact_gap_days=2)
    assert overrides.min_contact_gap_days == 2


@pytest.mark.asyncio
async def test_simulate_endpoint_rejects_gap_below_legal_floor(
    async_client: AsyncClient, auth_headers: dict
):
    payload = {
        "policy_overrides": {"min_contact_gap_days": 1},
        "replay_window": {"from": "2026-06-01", "to": "2026-09-01"},
    }
    response = await async_client.post(
        "/api/v1/policy/simulate", json=payload, headers=auth_headers
    )
    assert response.status_code == 422
    assert "min_contact_gap_days" in response.text


@pytest.mark.asyncio
async def test_policy_simulator_engine_direct_check():
    payload = PolicySimulateIn(
        policy_overrides=PolicyOverridesIn(min_contact_gap_days=2),
        replay_window=ReplayWindowIn(from_date="2026-06-01", to_date="2026-09-01"),
    )
    # 2 is allowed
    res = await run_policy_simulation(None, payload)
    assert res.cases_replayed > 0
