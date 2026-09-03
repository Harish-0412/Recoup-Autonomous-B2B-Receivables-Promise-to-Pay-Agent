"""The escalation ladder, as a finite state machine.

``monitoring -> reminded -> escalated -> human_handoff``, with ``closed``
reachable from anywhere. This is the component the "compliant escalation,
stopping rules" requirement is judged against, so the properties below are
enforced structurally rather than by convention:

* **``auto_transitions=False``.** Left at its default, ``transitions``
  synthesises a ``to_<state>()`` method for every state, and any code anywhere
  could call ``case.to_closed()`` or jump straight to ``human_handoff``,
  skipping the ladder. Disabling it means the four declared triggers are the
  *only* way a case can move.
* **Guards, not post-hoc checks.** ``contact_allowed`` and ``ladder_step_due``
  are ``conditions`` on the transitions themselves. A blocked transition does
  not happen -- the trigger returns ``False`` and the state is unchanged --
  rather than happening and being flagged afterwards.
* **Logging is attached to the transition, not to the caller.**
  ``log_transition`` runs as a ``before`` callback, so a state change that
  reaches the ledger is not something a call site has to remember to do.

One deliberate deviation from the reference design: the machine is built with
``send_event=True``. Without it, a callback reading ``self.trigger`` gets the
model's bound ``trigger`` *method* that ``transitions`` installs, not the name
of the firing event, and every audit line would read
``transition:<bound method ...>``. With it, callbacks receive an ``EventData``
carrying the real event name plus its source and destination states.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from transitions import Machine

from app.core.audit import DecisionLedger, append_decision_trace
from app.core.domain import CaseSnapshot
from app.models.enums import DecisionOutcome, EscalationState

if TYPE_CHECKING:  # pragma: no cover - types only
    from app.core.policy import PolicyEngine

#: The ladder's states, in order. Values match ``EscalationState`` exactly, so
#: the FSM's notion of state and the persisted/API notion cannot drift.
STATES: tuple[str, ...] = tuple(state.value for state in EscalationState)

#: Triggers that may fire without a human. ``close`` is excluded because a
#: closure is an outcome (paid, written off, disputed), not an escalation.
AUTOMATED_TRIGGERS: frozenset[str] = frozenset({"send_reminder", "escalate", "hand_off"})


class EscalationCase:
    """One invoice's position on the escalation ladder.

    Construct with the case snapshot the decision is about and the policy
    engine that owns the guards. The machine is instantiated per case: these
    objects are cheap, and sharing one across invoices would mean sharing
    ``ladder_index``.
    """

    states = list(STATES)

    #: Injected onto this model by ``Machine`` at construction time, and the
    #: authority on where the case currently is. Declared here so the attribute
    #: is visible to type checkers and to anyone reading the class -- a
    #: dynamically attached attribute that governs a safety-critical decision
    #: should not be invisible.
    state: str

    transitions: list[dict[str, Any]] = [
        {
            "trigger": "send_reminder",
            "source": "monitoring",
            "dest": "reminded",
            "conditions": "contact_allowed",
            "before": "log_transition",
        },
        {
            "trigger": "escalate",
            "source": "reminded",
            "dest": "escalated",
            "conditions": ["ladder_step_due", "contact_allowed"],
            "before": "log_transition",
        },
        {
            "trigger": "hand_off",
            "source": "escalated",
            "dest": "human_handoff",
            "before": "log_transition",
        },
        {
            "trigger": "close",
            "source": "*",
            "dest": "closed",
            "before": "log_transition",
        },
    ]

    def __init__(
        self,
        case: CaseSnapshot,
        policy_engine: PolicyEngine,
        *,
        ladder_index: int | None = None,
        initial: EscalationState | None = None,
        ledger: DecisionLedger | None = None,
    ) -> None:
        self.case = case
        self.invoice_id = case.invoice_id
        self.policy_engine = policy_engine
        self.ledger = ledger
        self.ladder_index = ladder_index if ladder_index is not None else case.invoice.ladder_index
        #: Refusals recorded this cycle, for the caller to surface.
        self.refusals: list[tuple[str, str]] = []

        start = (initial or case.invoice.escalation_state).value

        self.machine = Machine(
            model=self,
            states=self.states,
            transitions=self.transitions,
            initial=start,
            # Not optional -- see the module docstring.
            auto_transitions=False,
            # A trigger that is invalid for the current state is a refusal, not
            # a crash. A collections agent that raises mid-batch because one
            # case was out of order stops collecting for every other case too.
            ignore_invalid_triggers=True,
            send_event=True,
            after_state_change="advance_ladder_index",
        )

    # --- guards ------------------------------------------------------------

    def contact_allowed(self, event: Any = None) -> bool:
        """Whether policy permits putting a message in front of this customer.

        Delegates to the policy engine rather than re-implementing the checks,
        so the opt-out registry and the contact caps are enforced identically
        here and at the outbound gate. There is no path that sends a message
        without passing this.
        """

        return self.policy_engine.is_contact_allowed(self.case)

    def ladder_step_due(self, event: Any = None) -> bool:
        """Whether the next rung is due, and the ladder has a rung left."""

        return self.policy_engine.escalation_step_due(self.case, self.ladder_index)

    # --- callbacks ---------------------------------------------------------

    def advance_ladder_index(self, event: Any = None) -> None:
        """Move one rung along after any successful state change."""

        self.ladder_index += 1

    def log_transition(self, event: Any = None) -> None:
        """Write exactly one Decision Trace entry for this state change.

        Runs ``before`` the transition, so ``self.state`` is still the source
        state; the destination is read off the event.
        """

        trigger_name = getattr(getattr(event, "event", None), "name", "unknown")
        destination = getattr(getattr(event, "transition", None), "dest", "unknown")

        append_decision_trace(
            invoice_id=self.invoice_id,
            event=f"transition:{trigger_name}",
            outcome=DecisionOutcome.APPROVED,
            reason=f"{self.state} -> {destination} via {trigger_name}",
            ledger=self.ledger,
            from_state=self.state,
            to_state=destination,
            trigger=trigger_name,
            ladder_index=self.ladder_index,
            ladder_step=self.policy_engine.config.step_name(self.ladder_index),
        )

    # --- driving the machine ----------------------------------------------

    def try_trigger(self, trigger_name: str) -> bool:
        """Fire ``trigger_name``, recording a refusal if it does not move.

        ``transitions`` returns ``False`` both when a guard blocks and (given
        ``ignore_invalid_triggers``) when the trigger is not valid from the
        current state. Either way the case did not move, and a refusal is worth
        auditing: "the agent declined to escalate" is exactly the evidence the
        stopping rules need.
        """

        before_state = self.state
        fired = bool(self.machine.events[trigger_name].trigger(self))
        if fired:
            return True

        reason = self._refusal_reason(trigger_name, before_state)
        self.refusals.append((trigger_name, reason))
        append_decision_trace(
            invoice_id=self.invoice_id,
            event=f"transition_refused:{trigger_name}",
            outcome=DecisionOutcome.BLOCKED,
            reason=reason,
            ledger=self.ledger,
            from_state=before_state,
            trigger=trigger_name,
            ladder_index=self.ladder_index,
        )
        return False

    def _refusal_reason(self, trigger_name: str, state: str) -> str:
        """Explain, in one line, why a trigger did not fire."""

        if trigger_name not in self.available_triggers():
            return f"{trigger_name} is not a valid transition from {state}."
        # Checked in the order ``transitions`` evaluates the conditions, so the
        # reason reported is the guard that actually stopped it first.
        if trigger_name == "escalate" and not self.ladder_step_due():
            return (
                f"Escalation step {self.ladder_index} is not due yet "
                "(contact gap not elapsed, or ladder exhausted)."
            )
        if trigger_name in {"send_reminder", "escalate"} and not self.contact_allowed():
            probe = self.policy_engine.evaluate_contact_probe(self.case)
            return f"Policy blocked contact: {probe.reason}"
        return f"{trigger_name} was refused from {state}."

    def available_triggers(self) -> list[str]:
        """Triggers that are declared for the current state.

        Declared, not necessarily permitted -- a guard may still refuse. Used
        by the orchestrator to decide what to attempt, and by the tests to
        prove that a handed-off case has no automated move left.
        """

        return list(self.machine.get_triggers(self.state))

    def automated_triggers_available(self) -> list[str]:
        """The subset of :meth:`available_triggers` the agent may fire alone."""

        return [name for name in self.available_triggers() if name in AUTOMATED_TRIGGERS]

    @property
    def escalation_state(self) -> EscalationState:
        """The current state as the enum the rest of the app speaks."""

        return EscalationState(self.state)

    @property
    def is_terminal(self) -> bool:
        """Whether the agent is finished with this case, one way or another."""

        return self.escalation_state in {EscalationState.HUMAN_HANDOFF, EscalationState.CLOSED}
