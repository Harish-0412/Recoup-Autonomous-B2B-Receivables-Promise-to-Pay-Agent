"""The policy endpoint: what the gate is actually enforcing, right now.

Served straight from the same ``PolicyEngine`` the agent gates against, so the
rules this returns are the rules that ran -- not a copy in a document that
drifted. That is the whole reason the policy is compiled from config into data
rather than written as branching logic.

Read-only on purpose. Changing a discount ceiling or a contact cap is a
business decision with an audit requirement, and an unauthenticated demo API is
not where that should be possible. Ceilings move through configuration and a
restart, which leaves a deployment record.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.policy import PolicyEngine, policy_config_from_settings
from app.core.security import require_api_key
from app.core.tenancy import TenantContext, require_tenant
from app.db.session import get_db
from app.schemas.policy import PolicySimulateIn, PolicySimulateOut
from app.services.policy_simulator import run_policy_simulation

router = APIRouter(prefix="/policy", tags=["policy"], dependencies=[Depends(require_api_key)])


@router.get("")
async def get_policy() -> dict[str, Any]:
    """The active policy: configured ceilings plus the compiled rule set."""

    engine = PolicyEngine(policy_config_from_settings())
    description = engine.describe()
    description["enforcement"] = {
        "gate": "app.core.policy.PolicyEngine.evaluate_action",
        "escalation_guards": ["contact_allowed", "ladder_step_due"],
        "note": (
            "Every outbound action passes this gate. The escalation state "
            "machine calls the same engine for its guard conditions, so a "
            "transition cannot move a case into a state whose action the gate "
            "would refuse."
        ),
    }
    return description


@router.post("/simulate", response_model=PolicySimulateOut)
async def simulate_policy(
    payload: PolicySimulateIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> PolicySimulateOut:
    """Counterfactual replay of a policy change over one tenant's book."""
    return await run_policy_simulation(db, payload, tenant.business_id)
