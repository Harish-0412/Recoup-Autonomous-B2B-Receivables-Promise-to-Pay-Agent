"""Contracts for the policy simulation studio.

``POST /policy/simulate`` does not have an engine behind it yet: there is no
counterfactual replay over decision traces in the file tree, and this module
does not pretend otherwise. What it holds is the *contract* the engine will
serve when it ships, so the frontend studio and the backend can be built in
parallel against the same shape instead of against guesses.

Every override field mirrors a real ``PolicyConfig`` / ``ScoringConfig`` field
by name and bounds. A simulation that accepted knobs the production config
does not have would answer questions nobody can act on.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class PolicyOverridesIn(BaseModel):
    """Proposed ceilings under test. All optional; unset means production value."""

    model_config = ConfigDict(protected_namespaces=())

    discount_ceiling_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    max_discount_amount: float | None = Field(default=None, ge=0.0)
    min_contact_gap_days: int | None = Field(default=None, ge=0, le=30)
    max_contacts_per_invoice: int | None = Field(default=None, ge=0, le=20)
    min_days_overdue_to_contact: int | None = Field(default=None, ge=0, le=60)
    self_cure_probability: float | None = Field(default=None, ge=0.0, le=1.0)


class ReplayWindowIn(BaseModel):
    """The decision-trace window to counterfactually replay."""

    model_config = ConfigDict(protected_namespaces=())

    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")


class PolicySimulateIn(BaseModel):
    """One what-if question about the policy."""

    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)

    policy_overrides: PolicyOverridesIn = Field(default_factory=PolicyOverridesIn)
    replay_window: ReplayWindowIn


class SimulationMetrics(BaseModel):
    """One side of the before/after comparison."""

    model_config = ConfigDict(protected_namespaces=())

    recovery_rate: float = Field(ge=0.0, le=1.0)
    recovered_value: float = Field(ge=0.0)
    false_interventions: int = Field(ge=0)
    compliance_violations: int = Field(ge=0)


class SimulationDelta(BaseModel):
    """Simulated minus baseline, per metric. Sign meaning is metric-specific:
    up is good for recovery, bad for false interventions and violations."""

    model_config = ConfigDict(protected_namespaces=())

    recovery_rate: float
    recovered_value: float
    false_interventions: int
    compliance_violations: int


class AffectedCase(BaseModel):
    """One invoice whose tier would change under the proposed policy."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    baseline_tier: str
    simulated_tier: str
    reason_changed: str


class PolicySimulateOut(BaseModel):
    """The engine's answer, once the engine exists (see the 501 below)."""

    model_config = ConfigDict(protected_namespaces=())

    baseline: SimulationMetrics
    simulated: SimulationMetrics
    delta: SimulationDelta
    cases_affected: list[AffectedCase] = Field(default_factory=list)
    cases_replayed: int = Field(ge=0)
    engine_version: str = "unimplemented"
