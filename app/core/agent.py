"""The agentic loop: one decision cycle over one case.

This is the orchestrator the README's walkthrough describes, and it is
deliberately thin. It makes no judgements of its own -- it asks the scorer what
an invoice is worth, asks the policy engine whether the resulting action is
permitted, and asks the state machine to move the case. Every actual decision
belongs to one of those three, which is what makes each of them testable in
isolation and what keeps this function readable.

The order is the safety property:

    score -> propose -> **gate** -> transition -> execute

Nothing between "propose" and "execute" can skip the gate, because execution is
handed a ``PolicyDecision``, not a ``ProposedAction``. An executor with no
approved decision has nothing to send.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from app.core.audit import DecisionLedger, append_decision_trace
from app.core.domain import CaseSnapshot
from app.core.escalation import EscalationCase
from app.core.policy import (
    ActionType,
    PolicyConfig,
    PolicyDecision,
    PolicyEngine,
    ProposedAction,
)
from app.core.scorer import InvoiceScore, ScoringConfig, score_case
from app.models.enums import DecisionOutcome, EscalationState, InterventionTier

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from src.ml.recovery.scorer import RecoveryScorer


@lru_cache(maxsize=1)
def default_recovery_scorer() -> RecoveryScorer | None:
    """The configured scorer, built once per process.

    Imported lazily and cached because loading the model artifact and building
    its SHAP explainer is expensive and must not happen per invoice. The import
    is deferred rather than top-level so that ``app.core`` stays importable in
    an environment with no ML dependencies installed.

    ``get_recovery_scorer`` already returns the rules-based scorer when the
    flag is off or no artifact has been trained yet, so the only failure left
    to absorb here is the ML package being absent entirely. That returns
    ``None``, which routes ``score_case`` down its own built-in rules path --
    the one branch that needs no ML import at all. Returning a
    ``RulesBasedScorer`` here instead would be wrong: importing it requires the
    very package that just failed to import.
    """

    try:
        from src.ml.recovery.scorer import get_recovery_scorer

        return get_recovery_scorer()
    except Exception:  # pragma: no cover - environment without ML extras
        return None


#: The next rung from each state. The ladder -- not the scorer -- decides
#: *which* move comes next; the scorer only decides *whether* to move at all.
#:
#: Deriving the trigger from the tier instead is a bug worth naming, because it
#: looks right: a high-value case scores ESCALATE and tries to fire ``escalate``,
#: which is not a legal transition out of ``monitoring``, so the most urgent
#: invoices in the book would silently do nothing. Urgency changes how hard the
#: agent pushes, never how many rungs it skips.
NEXT_TRIGGER: dict[EscalationState, str] = {
    EscalationState.MONITORING: "send_reminder",
    EscalationState.REMINDED: "escalate",
    EscalationState.ESCALATED: "hand_off",
}


class CycleResult(BaseModel):
    """What one decision cycle did, and why."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    score: InvoiceScore
    tier: InterventionTier
    #: The action proposed, if the tier called for one.
    action: ProposedAction | None = None
    #: The gate's verdict on that action.
    decision: PolicyDecision | None = None
    #: Whether the case actually moved.
    transitioned: bool = False
    state_before: EscalationState
    state_after: EscalationState
    ladder_step: str = ""
    reason: str = ""
    #: True when the agent has finished with this case for good.
    terminal: bool = False

    @property
    def acted(self) -> bool:
        """Whether this cycle produced an approved, executable action."""

        return bool(self.decision and self.decision.allowed and self.transitioned)


class AgentConfig(BaseModel):
    """Everything one cycle needs to be reproducible."""

    model_config = ConfigDict(protected_namespaces=())

    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


def _propose(
    case: CaseSnapshot,
    tier: InterventionTier,
    trigger: str,
    policy_config: PolicyConfig,
) -> ProposedAction:
    """Turn the next ladder rung into a concrete proposal.

    The rung fixes the *kind* of action; the tier fixes how hard it pushes. A
    settlement discount is attached only when escalating a high-value case, and
    only up to the configured ceiling -- the agent asks for the ceiling, it does
    not invent a number, and the gate independently clamps what it asks for.

    Handing a case to a human is proposed as ``HAND_OFF``, which the contact
    caps deliberately do not apply to. If they did, an invoice that hit its cap
    could never be handed over and would sit in the agent's queue with nobody
    told about it -- the opposite of a stopping rule.
    """

    ladder_step = policy_config.step_name(case.invoice.ladder_index)
    channel = case.customer.preferred_channel

    if trigger == "hand_off":
        return ProposedAction(
            invoice_id=case.invoice_id,
            action_type=ActionType.HAND_OFF,
            channel=channel,
            ladder_step=ladder_step,
            rationale="Escalation ladder exhausted; handing to a human.",
        )

    if trigger == "escalate":
        escalating = tier is InterventionTier.ESCALATE
        return ProposedAction(
            invoice_id=case.invoice_id,
            action_type=ActionType.ESCALATE,
            channel=channel,
            ladder_step=ladder_step,
            requested_discount_pct=(policy_config.discount_ceiling_pct if escalating else 0.0),
            rationale=(
                "High value at risk; escalating with a bounded settlement offer."
                if escalating
                else "Advancing one rung without a settlement offer."
            ),
        )

    return ProposedAction(
        invoice_id=case.invoice_id,
        action_type=ActionType.SEND_REMINDER,
        channel=channel,
        ladder_step=ladder_step,
        rationale="Value at risk warrants contact; a reminder is the first rung.",
    )


def run_cycle(
    case: CaseSnapshot,
    *,
    config: AgentConfig | None = None,
    engine: PolicyEngine | None = None,
    ledger: DecisionLedger | None = None,
    scorer: RecoveryScorer | None = None,
) -> CycleResult:
    """Run one full decision cycle over one case.

    ``scorer`` selects where P(recovery) comes from. Left at ``None`` the
    configured default applies, which is the trained model when
    ``USE_MODEL_SCORER`` is set and an artifact exists, and the hand-written
    rules otherwise.
    """

    settings = config or AgentConfig()
    policy_engine = engine or PolicyEngine(settings.policy, ledger=ledger)
    active_scorer = scorer if scorer is not None else default_recovery_scorer()

    score = score_case(case, settings.scoring, active_scorer)
    fsm = EscalationCase(case, policy_engine, ledger=ledger)
    state_before = fsm.escalation_state

    append_decision_trace(
        invoice_id=case.invoice_id,
        event="scored",
        outcome=DecisionOutcome.APPROVED,
        reason=score.rationale,
        ledger=ledger,
        p_recovery=score.p_recovery,
        expected_value=score.expected_value,
        outstanding=score.outstanding,
        tier=score.tier.value,
        scorer_version=score.prediction.model_version,
        top_drivers=[driver.feature for driver in score.prediction.top_drivers[:3]],
    )

    if score.tier is InterventionTier.WAIT:
        return CycleResult(
            invoice_id=case.invoice_id,
            score=score,
            tier=score.tier,
            state_before=state_before,
            state_after=state_before,
            reason=score.rationale,
            terminal=fsm.is_terminal,
        )

    trigger = NEXT_TRIGGER.get(state_before)
    if trigger is None:
        # human_handoff and closed are terminal: the agent is finished here.
        return CycleResult(
            invoice_id=case.invoice_id,
            score=score,
            tier=score.tier,
            state_before=state_before,
            state_after=state_before,
            reason=f"Case is {state_before.value}; no automated action remains.",
            terminal=True,
        )

    action = _propose(case, score.tier, trigger, policy_engine.config)
    decision = policy_engine.evaluate_action(case, action, ledger=ledger)

    if not decision.allowed:
        return CycleResult(
            invoice_id=case.invoice_id,
            score=score,
            tier=score.tier,
            action=action,
            decision=decision,
            state_before=state_before,
            state_after=state_before,
            ladder_step=action.ladder_step,
            reason=decision.reason,
            terminal=fsm.is_terminal,
        )

    # The gate approved it; now ask the ladder whether this case may move. Both
    # must agree, and the FSM re-checks the same guards -- belt and braces on
    # the one path that puts a message in front of a customer.
    moved = fsm.try_trigger(trigger)

    if (
        not moved
        and trigger == "escalate"
        and policy_engine.config.is_ladder_exhausted(fsm.ladder_index)
    ):
        # The rung was refused and there are no rungs left: hand over rather
        # than retrying. This is the stopping rule -- the agent runs out of
        # ladder and stops, instead of looping on a case it cannot progress.
        handoff = _propose(case, score.tier, "hand_off", policy_engine.config)
        if policy_engine.evaluate_action(case, handoff, ledger=ledger).allowed:
            moved = fsm.try_trigger("hand_off")

    reason = (
        decision.reason if moved else (fsm.refusals[-1][1] if fsm.refusals else decision.reason)
    )

    return CycleResult(
        invoice_id=case.invoice_id,
        score=score,
        tier=score.tier,
        action=action,
        decision=decision,
        transitioned=moved,
        state_before=state_before,
        state_after=fsm.escalation_state,
        ladder_step=action.ladder_step,
        reason=reason,
        terminal=fsm.is_terminal,
    )


def run_batch(
    cases: list[CaseSnapshot],
    *,
    config: AgentConfig | None = None,
    ledger: DecisionLedger | None = None,
    scorer: RecoveryScorer | None = None,
) -> list[CycleResult]:
    """Run one cycle over every case, highest expected value first.

    One ``PolicyEngine`` and one ``RecoveryScorer`` are built for the batch
    rather than per case. For the policy engine, compiling the rule data is the
    expensive part; for the scorer it is deserialising the model and building
    its SHAP explainer, which must happen once per run and not once per
    invoice.
    """

    settings = config or AgentConfig()
    engine = PolicyEngine(settings.policy, ledger=ledger)
    active_scorer = scorer if scorer is not None else default_recovery_scorer()

    scored = sorted(
        ((case, score_case(case, settings.scoring, active_scorer)) for case in cases),
        key=lambda pair: pair[1].expected_value,
        reverse=True,
    )
    return [
        run_cycle(case, config=settings, engine=engine, ledger=ledger, scorer=active_scorer)
        for case, _ in scored
    ]
