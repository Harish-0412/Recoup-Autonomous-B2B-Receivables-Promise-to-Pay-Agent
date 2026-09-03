"""Tests for the policy gate.

The gate is the one component whose failure is a compliance incident rather
than a bug, so these tests are about what it *refuses*. A gate that only gets
tested on the paths it approves has not been tested.
"""

from __future__ import annotations

import pytest

from app.core.policy import (
    ActionType,
    PolicyConfig,
    PolicyEngine,
    ProposedAction,
    build_policy_rules,
    policy_config_from_settings,
)
from app.models.enums import ContactChannel, DecisionOutcome
from tests.conftest import make_case


def remind(invoice_id: str = "INV-1", **kwargs: object) -> ProposedAction:
    return ProposedAction(invoice_id=invoice_id, action_type=ActionType.SEND_REMINDER, **kwargs)  # type: ignore[arg-type]


class TestBlocking:
    def test_a_clean_case_is_approved(self, engine):
        decision = engine.evaluate(make_case(), remind())

        assert decision.allowed
        assert decision.violations == []

    def test_opt_out_blocks(self, engine):
        case = make_case(opted_out=frozenset({ContactChannel.EMAIL}))

        decision = engine.evaluate(case, remind(channel=ContactChannel.EMAIL))

        assert not decision.allowed
        assert [v.code for v in decision.violations] == ["opt_out"]

    def test_opt_out_on_one_channel_does_not_block_another(self, engine):
        case = make_case(opted_out=frozenset({ContactChannel.WHATSAPP}))

        decision = engine.evaluate(case, remind(channel=ContactChannel.EMAIL))

        assert decision.allowed

    def test_contact_gap_blocks(self, engine):
        decision = engine.evaluate(make_case(days_since_last_contact=2), remind())

        assert not decision.allowed
        assert "contact_frequency_cap" in [v.code for v in decision.violations]

    def test_contact_gap_boundary_is_inclusive(self, engine):
        """Exactly the configured gap is allowed; one day less is not."""

        assert engine.evaluate(make_case(days_since_last_contact=3), remind()).allowed
        assert not engine.evaluate(make_case(days_since_last_contact=2), remind()).allowed

    def test_volume_cap_blocks_at_the_limit_not_after_it(self, engine):
        assert engine.evaluate(make_case(prior_reminders_sent=3), remind()).allowed
        assert not engine.evaluate(make_case(prior_reminders_sent=4), remind()).allowed

    def test_an_invoice_not_yet_overdue_is_not_contacted(self, engine):
        decision = engine.evaluate(make_case(days_overdue=0), remind())

        assert not decision.allowed
        assert "not_yet_overdue" in [v.code for v in decision.violations]

    def test_an_open_undue_promise_silences_reminders(self, engine):
        case = make_case(has_open_promise=True, open_promise_due_in_days=4)

        decision = engine.evaluate(case, remind())

        assert not decision.allowed
        assert "promise_open" in [v.code for v in decision.violations]

    def test_a_promise_past_its_date_no_longer_silences_reminders(self, engine):
        case = make_case(has_open_promise=True, open_promise_due_in_days=-2)

        assert engine.evaluate(case, remind()).allowed

    def test_escalation_is_not_silenced_by_the_promise_it_reacts_to(self, engine):
        """A broken promise must still be escalatable.

        The quiet-while-promised rule applies to routine reminders only. If it
        applied to escalation too, a customer could stop the agent permanently
        by promising and never paying.
        """

        case = make_case(has_open_promise=True, open_promise_due_in_days=1)
        escalation = ProposedAction(invoice_id="INV-1", action_type=ActionType.ESCALATE)

        assert engine.evaluate(case, escalation).allowed

    def test_all_violations_are_reported_not_just_the_first(self, engine):
        """A blocked action must carry every reason it was blocked.

        An audit record saying "blocked by the contact cap" when it was also an
        opt-out is a misleading record.
        """

        case = make_case(
            days_since_last_contact=1,
            prior_reminders_sent=9,
            opted_out=frozenset({None}),
        )

        decision = engine.evaluate(case, remind())

        assert sorted(v.code for v in decision.violations) == [
            "contact_frequency_cap",
            "contact_volume_cap",
            "opt_out",
        ]


class TestNonContactActions:
    def test_contact_rules_do_not_block_a_handoff(self, engine):
        """Handing a case to a human is not a contact and must never be capped.

        If contact caps blocked handoff, an invoice that hit its cap would be
        stuck in the agent's queue forever with nobody told about it -- the
        exact opposite of a stopping rule.
        """

        case = make_case(
            days_since_last_contact=0,
            prior_reminders_sent=99,
            opted_out=frozenset({None}),
        )
        handoff = ProposedAction(invoice_id="INV-1", action_type=ActionType.HAND_OFF)

        assert engine.evaluate(case, handoff).allowed

    def test_contact_rules_do_not_block_closing_a_case(self, engine):
        case = make_case(opted_out=frozenset({None}))
        close = ProposedAction(invoice_id="INV-1", action_type=ActionType.CLOSE)

        assert engine.evaluate(case, close).allowed


class TestDiscountCeiling:
    def test_a_request_over_the_ceiling_is_clamped_not_rejected(self, engine):
        offer = ProposedAction(
            invoice_id="INV-1",
            action_type=ActionType.OFFER_SETTLEMENT,
            requested_discount_pct=35.0,
        )

        decision = engine.evaluate(make_case(amount=100_000.0), offer)

        assert decision.allowed
        assert decision.effective_discount_pct == 10.0
        assert decision.effective_discount_amount == 10_000.0
        assert decision.adjustments[0].requested == 35.0

    def test_a_request_under_the_ceiling_is_untouched(self, engine):
        offer = ProposedAction(
            invoice_id="INV-1",
            action_type=ActionType.OFFER_SETTLEMENT,
            requested_discount_pct=4.0,
        )

        decision = engine.evaluate(make_case(amount=100_000.0), offer)

        assert decision.effective_discount_pct == 4.0
        assert decision.adjustments == []

    def test_the_absolute_rupee_ceiling_applies_on_top_of_the_percentage(self):
        engine = PolicyEngine(PolicyConfig(discount_ceiling_pct=10.0, max_discount_amount=15_000.0))
        offer = ProposedAction(
            invoice_id="INV-1",
            action_type=ActionType.OFFER_SETTLEMENT,
            requested_discount_pct=10.0,
        )

        # 10% of 10,00,000 would be 1,00,000 -- the absolute cap wins.
        decision = engine.evaluate(make_case(amount=1_000_000.0), offer)

        assert decision.effective_discount_amount == 15_000.0
        assert decision.effective_discount_pct == pytest.approx(1.5)

    def test_the_gate_can_never_raise_a_discount(self, engine):
        """There is no rule action that increases a number. Prove it."""

        for requested in (0.0, 2.5, 9.99, 10.0, 50.0, 100.0):
            offer = ProposedAction(
                invoice_id="INV-1",
                action_type=ActionType.OFFER_SETTLEMENT,
                requested_discount_pct=requested,
            )
            decision = engine.evaluate(make_case(), offer)

            assert decision.effective_discount_pct <= requested
            assert decision.effective_discount_pct <= 10.0


class TestAuditing:
    def test_evaluate_action_records_an_approval(self, engine, ledger):
        engine.evaluate_action(make_case(), remind(), ledger=ledger)

        (entry,) = ledger.entries()
        assert entry.event == "policy:send_reminder"
        assert entry.outcome is DecisionOutcome.APPROVED

    def test_evaluate_action_records_a_block_with_its_reasons(self, engine, ledger):
        case = make_case(opted_out=frozenset({None}))

        engine.evaluate_action(case, remind(), ledger=ledger)

        (entry,) = ledger.entries()
        assert entry.outcome is DecisionOutcome.BLOCKED
        assert entry.payload["violations"] == ["opt_out"]
        assert "opted out" in entry.reason

    def test_bare_evaluate_does_not_write_to_the_ledger(self, engine, ledger):
        """Guard checks must not flood the audit trail.

        The FSM calls ``evaluate`` many times per cycle for its guards; if that
        wrote entries, the trail would be unreadable and the entry counts in
        the batch report meaningless.
        """

        for _ in range(5):
            engine.evaluate(make_case(), remind())

        assert len(ledger) == 0


class TestRulesAreData:
    def test_the_config_compiles_into_inspectable_rules(self, policy_config):
        rules = build_policy_rules(policy_config)

        assert len(rules) >= 5
        assert all("conditions" in rule and "actions" in rule for rule in rules)

    def test_changing_the_config_changes_the_rules(self):
        strict = build_policy_rules(PolicyConfig(min_contact_gap_days=14))

        gap_values = [
            condition["value"]
            for rule in strict
            for condition in rule["conditions"]["all"]
            if condition["name"] == "days_since_last_contact"
        ]
        assert gap_values == [14]

    def test_describe_serialises_for_the_api(self, engine):
        described = engine.describe()

        assert described["rule_count"] == len(described["rules"])
        assert described["config"]["discount_ceiling_pct"] == 10.0

    def test_settings_produce_a_usable_policy(self):
        config = policy_config_from_settings()

        assert config.ladder_length >= 1
        assert config.escalation_ladder[-1] == "human_handoff"


class TestGuardsMatchTheGate:
    def test_is_contact_allowed_agrees_with_evaluate(self, engine):
        """The FSM guard and the outbound gate must never disagree.

        If they could, the machine would move a case into a state whose action
        the gate then refuses, and the case would sit in a state it never
        earned.
        """

        cases = [
            make_case(),
            make_case(days_since_last_contact=1),
            make_case(prior_reminders_sent=4),
            make_case(opted_out=frozenset({None})),
            make_case(days_overdue=0),
        ]

        for case in cases:
            gate = engine.evaluate(case, remind(channel=case.customer.preferred_channel))
            assert engine.is_contact_allowed(case) == gate.allowed
