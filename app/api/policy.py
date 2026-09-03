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

from fastapi import APIRouter

from app.core.policy import PolicyEngine, policy_config_from_settings

router = APIRouter(prefix="/policy", tags=["policy"])


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
