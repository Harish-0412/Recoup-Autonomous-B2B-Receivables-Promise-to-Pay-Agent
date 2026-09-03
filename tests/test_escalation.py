"""Tests for the escalation ladder.

This is the safety-critical component: "compliant escalation, stopping rules"
is judged directly against it. So these tests are written to prove the guards
*block*, not merely that the happy path works -- a state machine whose guards
are never exercised is a state machine with no guards.
"""

from __future__ import annotations

import pytest

from app.core.escalation import AUTOMATED_TRIGGERS, EscalationCase
from app.core.policy import PolicyConfig, PolicyEngine
from app.models.enums import ContactChannel, DecisionOutcome, EscalationState
from tests.conftest import make_case


class TestMachineConstruction:
    def test_auto_transitions_are_disabled(self, engine):
        """``to_<state>()`` must not exist.

        With ``auto_transitions`` left at its default, ``transitions`` would
        generate a ``to_closed()``/``to_human_handoff()`` for every state, and
        any code could jump a case straight past the ladder. This is the test
        that would catch that regression.
        """

        case = EscalationCase(make_case(), engine)

        for state in EscalationState:
            assert not hasattr(case, f"to_{state.value}"), (
                f"to_{state.value}() exists -- auto_transitions is not disabled, "
                "and the escalation ladder can be bypassed"
            )

    def test_only_the_four_declared_triggers_exist(self, engine):
        case = EscalationCase(make_case(), engine)
        declared = set(case.machine.events)

        assert declared == {"send_reminder", "escalate", "hand_off", "close"}

    def test_starts_in_the_state_the_case_carries(self, engine):
        case = EscalationCase(make_case(), engine, initial=EscalationState.ESCALATED)
        assert case.escalation_state is EscalationState.ESCALATED


class TestGuardsBlock:
    def test_reminder_is_refused_when_the_contact_gap_has_not_elapsed(self, engine):
        case = EscalationCase(make_case(days_since_last_contact=1), engine)

        assert case.try_trigger("send_reminder") is False
        assert case.state == "monitoring"

    def test_escalation_is_refused_when_the_step_is_not_due(self, engine, ledger):
        case = EscalationCase(
            make_case(days_since_last_contact=1),
            engine,
            initial=EscalationState.REMINDED,
            ledger=ledger,
        )

        assert case.try_trigger("escalate") is False
        assert case.state == "reminded"
        assert case.refusals, "a refusal must be recorded, not silently swallowed"

    def test_opt_out_blocks_every_contacting_trigger(self, engine):
        """Opt-out overrides value, urgency and ladder timing alike."""

        opted_out = make_case(opted_out=frozenset({ContactChannel.EMAIL}))

        monitoring = EscalationCase(opted_out, engine)
        assert monitoring.try_trigger("send_reminder") is False
        assert monitoring.state == "monitoring"

        reminded = EscalationCase(opted_out, engine, initial=EscalationState.REMINDED)
        assert reminded.try_trigger("escalate") is False
        assert reminded.state == "reminded"

    def test_a_global_opt_out_blocks_a_specific_channel(self, engine):
        case = EscalationCase(make_case(opted_out=frozenset({None})), engine)

        assert case.try_trigger("send_reminder") is False

    def test_contact_volume_cap_blocks_further_reminders(self, engine):
        capped = make_case(prior_reminders_sent=4)
        case = EscalationCase(capped, engine)

        assert case.try_trigger("send_reminder") is False


class TestLadderWalk:
    def test_full_walk_to_human_handoff(self, engine, ledger):
        """monitoring -> reminded -> escalated -> human_handoff, end to end."""

        case = EscalationCase(make_case(), engine, ledger=ledger)

        assert case.try_trigger("send_reminder") is True
        assert case.escalation_state is EscalationState.REMINDED

        assert case.try_trigger("escalate") is True
        assert case.escalation_state is EscalationState.ESCALATED

        assert case.try_trigger("hand_off") is True
        assert case.escalation_state is EscalationState.HUMAN_HANDOFF
        assert case.is_terminal

    def test_a_handed_off_case_has_no_automated_move_left(self, engine):
        """The stopping rule: the agent runs out of rungs and stops."""

        case = EscalationCase(make_case(), engine, initial=EscalationState.HUMAN_HANDOFF)

        assert case.automated_triggers_available() == []
        assert case.available_triggers() == ["close"]

        for trigger in AUTOMATED_TRIGGERS:
            assert case.try_trigger(trigger) is False
            assert case.escalation_state is EscalationState.HUMAN_HANDOFF

        assert case.try_trigger("close") is True
        assert case.escalation_state is EscalationState.CLOSED

    def test_the_ladder_cannot_be_walked_backwards(self, engine):
        case = EscalationCase(make_case(), engine, initial=EscalationState.ESCALATED)

        assert case.try_trigger("send_reminder") is False
        assert case.escalation_state is EscalationState.ESCALATED

    def test_ladder_index_advances_on_each_successful_move(self, engine):
        case = EscalationCase(make_case(), engine, ladder_index=0)

        case.try_trigger("send_reminder")
        assert case.ladder_index == 1
        case.try_trigger("escalate")
        assert case.ladder_index == 2

    def test_ladder_index_does_not_advance_on_a_refusal(self, engine):
        case = EscalationCase(make_case(days_since_last_contact=1), engine)

        assert case.try_trigger("send_reminder") is False
        assert case.ladder_index == 0

    def test_escalation_stops_once_the_ladder_is_exhausted(self):
        """A two-rung ladder must not permit a third rung."""

        engine = PolicyEngine(PolicyConfig(escalation_ladder=("reminder_1", "human_handoff")))
        case = EscalationCase(make_case(), engine, initial=EscalationState.REMINDED, ladder_index=2)

        assert engine.escalation_step_due(case.case, case.ladder_index) is False
        assert case.try_trigger("escalate") is False


class TestAuditTrail:
    def test_each_successful_transition_writes_exactly_one_entry(self, engine, ledger):
        case = EscalationCase(make_case(), engine, ledger=ledger)

        case.try_trigger("send_reminder")
        case.try_trigger("escalate")
        case.try_trigger("hand_off")

        transitions = [e for e in ledger if e.event.startswith("transition:")]
        assert len(transitions) == 3
        assert [e.event for e in transitions] == [
            "transition:send_reminder",
            "transition:escalate",
            "transition:hand_off",
        ]

    def test_the_event_name_is_the_trigger_not_a_bound_method(self, engine, ledger):
        """Regression guard.

        Reading ``self.trigger`` inside a callback yields the model's bound
        ``trigger`` method that ``transitions`` installs, not the firing
        event's name -- which would put ``<bound method ...>`` in every audit
        line. The machine is built with ``send_event=True`` to avoid that.
        """

        case = EscalationCase(make_case(), engine, ledger=ledger)
        case.try_trigger("send_reminder")

        entry = ledger.entries()[0]
        assert entry.event == "transition:send_reminder"
        assert "bound method" not in entry.reason
        assert entry.payload["trigger"] == "send_reminder"
        assert entry.payload["from_state"] == "monitoring"
        assert entry.payload["to_state"] == "reminded"

    def test_a_refused_transition_is_audited_as_blocked(self, engine, ledger):
        case = EscalationCase(make_case(days_since_last_contact=1), engine, ledger=ledger)

        case.try_trigger("send_reminder")

        (entry,) = ledger.entries()
        assert entry.event == "transition_refused:send_reminder"
        assert entry.outcome is DecisionOutcome.BLOCKED
        assert "gap" in entry.reason.lower() or "opted out" in entry.reason.lower()

    def test_no_transition_writes_to_the_ledger_without_moving(self, engine, ledger):
        """A blocked transition must not leave an 'approved' entry behind."""

        case = EscalationCase(make_case(opted_out=frozenset({None})), engine, ledger=ledger)
        case.try_trigger("send_reminder")

        assert not [e for e in ledger if e.event.startswith("transition:")]

    def test_the_ledger_chain_still_verifies_after_a_full_walk(self, engine, ledger):
        case = EscalationCase(make_case(), engine, ledger=ledger)
        for trigger in ("send_reminder", "escalate", "hand_off", "close"):
            case.try_trigger(trigger)

        ledger.verify()
        assert ledger.is_valid()


@pytest.mark.parametrize(
    "state,expected",
    [
        (EscalationState.MONITORING, ["send_reminder"]),
        (EscalationState.REMINDED, ["escalate"]),
        (EscalationState.ESCALATED, ["hand_off"]),
        (EscalationState.HUMAN_HANDOFF, []),
        (EscalationState.CLOSED, []),
    ],
)
def test_automated_triggers_available_per_state(engine, state, expected):
    case = EscalationCase(make_case(), engine, initial=state)

    assert case.automated_triggers_available() == expected
