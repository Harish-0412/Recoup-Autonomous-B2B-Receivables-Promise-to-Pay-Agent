"""End-to-end tests for the decision cycle and the evaluation report.

These are the tests that would catch a component being wired up wrongly even
though each part passes its own tests -- most importantly, that the policy gate
is on the path and cannot be routed around.
"""

from __future__ import annotations

from app.core.agent import AgentConfig, run_batch, run_cycle
from app.core.evaluation import build_report, count_compliance_violations, format_inr
from app.core.policy import PolicyConfig
from app.core.scorer import ScoringConfig
from app.models.enums import ContactChannel, EscalationState, InterventionTier
from tests.conftest import make_case

BIG_AT_RISK = {
    "amount": 2_000_000.0,
    "days_overdue": 45,
    "on_time_ratio": 0.25,
    "avg_days_late": 30.0,
    "broken_promises": 8,
}


class TestCycle:
    def test_a_wait_decision_takes_no_action_and_moves_nothing(self, ledger):
        result = run_cycle(make_case(amount=6_000.0, days_overdue=3), ledger=ledger)

        assert result.tier is InterventionTier.WAIT
        assert result.action is None
        assert result.decision is None
        assert result.transitioned is False
        assert result.state_after is result.state_before

    def test_a_wait_decision_still_records_why(self, ledger):
        run_cycle(make_case(amount=6_000.0, days_overdue=3), ledger=ledger)

        (entry,) = ledger.entries()
        assert entry.event == "scored"
        assert entry.payload["tier"] == "WAIT"
        assert entry.reason

    def test_an_actionable_case_is_scored_gated_and_moved_in_that_order(self, ledger):
        result = run_cycle(make_case(**BIG_AT_RISK), ledger=ledger)

        assert result.acted
        assert result.state_after is EscalationState.REMINDED

        events = [entry.event for entry in ledger]
        assert events.index("scored") < events.index("policy:send_reminder")
        assert events.index("policy:send_reminder") < events.index("transition:send_reminder")

    def test_urgency_does_not_let_a_case_skip_a_rung(self, ledger):
        """Even the most urgent invoice starts at the first rung.

        A high-value case scores ESCALATE, but from ``monitoring`` the only
        legal move is a reminder. Urgency governs how hard the agent pushes,
        never how many rungs it skips.
        """

        result = run_cycle(make_case(**BIG_AT_RISK), ledger=ledger)

        assert result.tier is InterventionTier.ESCALATE
        assert result.action is not None
        assert result.action.action_type.value == "SEND_REMINDER"
        assert result.state_after is EscalationState.REMINDED

    def test_a_blocked_action_never_reaches_a_transition(self, ledger):
        """The core safety property of the whole system."""

        blocked = make_case(opted_out=frozenset({None}), **BIG_AT_RISK)

        result = run_cycle(blocked, ledger=ledger)

        assert result.decision is not None
        assert result.decision.allowed is False
        assert result.transitioned is False
        assert result.state_after is EscalationState.MONITORING
        assert not [e for e in ledger if e.event.startswith("transition:")]

    def test_a_blocked_action_explains_itself(self, ledger):
        blocked = make_case(days_since_last_contact=1, **BIG_AT_RISK)

        result = run_cycle(blocked, ledger=ledger)

        assert "Blocked" in result.reason
        assert result.decision is not None
        assert result.decision.violations

    def test_the_escalation_offer_is_clamped_to_the_ceiling(self, ledger):
        config = AgentConfig(policy=PolicyConfig(discount_ceiling_pct=7.5))

        result = run_cycle(make_case(**BIG_AT_RISK), config=config, ledger=ledger)

        assert result.decision is not None
        assert result.decision.effective_discount_pct <= 7.5

    def test_the_agent_never_proposes_a_discount_above_the_ceiling(self, ledger):
        """The proposal itself is bounded, not merely the gate's verdict.

        Belt and braces: even if the gate were removed, the agent would not be
        asking for an unbounded number.
        """

        config = AgentConfig(policy=PolicyConfig(discount_ceiling_pct=5.0))

        result = run_cycle(make_case(**BIG_AT_RISK), config=config, ledger=ledger)

        assert result.action is not None
        assert result.action.requested_discount_pct <= 5.0

    def test_the_case_channel_is_respected(self, ledger):
        case = make_case(**BIG_AT_RISK)
        case = case.model_copy(
            update={
                "customer": case.customer.model_copy(
                    update={"preferred_channel": ContactChannel.WHATSAPP}
                )
            }
        )

        result = run_cycle(case, ledger=ledger)

        assert result.action is not None
        assert result.action.channel is ContactChannel.WHATSAPP

    def test_the_ledger_verifies_after_a_cycle(self, ledger):
        run_cycle(make_case(**BIG_AT_RISK), ledger=ledger)

        ledger.verify()


class TestBatch:
    def test_a_batch_is_processed_highest_value_first(self, ledger):
        cases = [
            make_case(invoice_id="small", amount=15_000.0, days_overdue=4),
            make_case(invoice_id="huge", **BIG_AT_RISK),
            make_case(invoice_id="mid", amount=200_000.0, days_overdue=15),
        ]

        results = run_batch(cases, ledger=ledger)

        assert results[0].invoice_id == "huge"
        assert len(results) == 3

    def test_every_case_gets_a_decision(self, ledger):
        cases = [make_case(invoice_id=f"INV-{i}", amount=50_000.0 * (i + 1)) for i in range(10)]

        results = run_batch(cases, ledger=ledger)

        assert {r.invoice_id for r in results} == {c.invoice_id for c in cases}
        assert all(r.reason for r in results)


class TestReport:
    def test_the_report_counts_what_happened(self, ledger):
        cases = [
            make_case(invoice_id="act", **BIG_AT_RISK),
            make_case(invoice_id="wait", amount=6_000.0, days_overdue=3),
            make_case(invoice_id="blocked", opted_out=frozenset({None}), **BIG_AT_RISK),
        ]

        results = run_batch(cases, ledger=ledger)
        report = build_report(results, ledger=ledger)

        assert report.invoices_processed == 3
        assert report.left_alone == 1
        assert report.flagged_for_intervention == 2
        assert report.interventions_executed == 1
        assert report.blocked_by_policy == 1
        assert report.policy_block_reasons == {"opt_out": 1}

    def test_a_clean_run_reports_no_compliance_violations(self, ledger):
        cases = [make_case(invoice_id=f"INV-{i}", **BIG_AT_RISK) for i in range(20)]

        results = run_batch(cases, ledger=ledger)
        report = build_report(results, ledger=ledger)

        assert report.compliance_violations == 0
        assert report.ledger_verified is True

    def test_a_block_then_a_later_action_is_not_a_violation(self, ledger):
        """Across cycles, blocked-then-allowed is the cap working, not a breach.

        This is the case that made a naive set-based detector report hundreds
        of false violations.
        """

        blocked_case = make_case(days_since_last_contact=1, **BIG_AT_RISK)
        run_cycle(blocked_case, ledger=ledger)

        allowed_case = make_case(days_since_last_contact=30, **BIG_AT_RISK)
        run_cycle(allowed_case, ledger=ledger)

        assert count_compliance_violations(ledger) == 0

    def test_an_action_taken_while_blocked_is_a_violation(self, ledger):
        """The detector must actually detect. Forge the sequence and check."""

        from app.core.audit import append_decision_trace
        from app.models.enums import DecisionOutcome

        append_decision_trace(
            invoice_id="INV-BAD",
            event="policy:send_reminder",
            outcome=DecisionOutcome.BLOCKED,
            ledger=ledger,
        )
        append_decision_trace(
            invoice_id="INV-BAD",
            event="transition:send_reminder",
            outcome=DecisionOutcome.APPROVED,
            ledger=ledger,
            trigger="send_reminder",
        )

        assert count_compliance_violations(ledger) == 1

    def test_ground_truth_rows_appear_only_when_outcomes_are_supplied(self, ledger):
        cases = [make_case(invoice_id="a", **BIG_AT_RISK)]
        results = run_batch(cases, ledger=ledger)

        without = build_report(results, ledger=ledger)
        assert without.recovered_count is None
        assert "Recovery rate" not in without.render()

        with_truth = build_report(results, ledger=ledger, outcomes={"a": True})
        assert with_truth.recovered_count == 1
        assert "Recovery rate" in with_truth.render()

    def test_a_contacted_invoice_that_would_have_paid_counts_as_a_false_intervention(self, ledger):
        results = run_batch([make_case(invoice_id="a", **BIG_AT_RISK)], ledger=ledger)

        report = build_report(results, ledger=ledger, outcomes={"a": True})

        assert report.false_interventions == 1

    def test_the_rendered_table_is_aligned_text(self, ledger):
        results = run_batch([make_case(**BIG_AT_RISK)], ledger=ledger)

        rendered = build_report(results, ledger=ledger).render()

        assert "Invoices processed:" in rendered
        assert "Ledger hash chain verified:" in rendered
        assert rendered.rstrip().endswith("yes")


class TestFormatting:
    def test_indian_number_formatting(self):
        assert format_inr(45_000) == "Rs 45,000"
        assert format_inr(4_12_000) == "Rs 4.1L"
        assert format_inr(1_84_00_000) == "Rs 1.84 Cr"


def test_a_full_ladder_walk_reaches_handoff_and_stops(ledger):
    """The stopping rule, driven through the real orchestrator.

    Each cycle re-presents the case as if the contact gap had elapsed, which is
    what the batch demo's clock does. The agent must climb the ladder and then
    stop, rather than escalating indefinitely.
    """

    config = AgentConfig(
        policy=PolicyConfig(max_contacts_per_invoice=99),
        scoring=ScoringConfig(),
    )
    case = make_case(**BIG_AT_RISK)
    states: list[EscalationState] = []

    for _ in range(6):
        result = run_cycle(case, config=config, ledger=ledger)
        states.append(result.state_after)
        case = case.model_copy(
            update={
                "invoice": case.invoice.model_copy(
                    update={
                        "escalation_state": result.state_after,
                        "ladder_index": case.invoice.ladder_index + (1 if result.acted else 0),
                        "days_since_last_contact": 30,
                    }
                )
            }
        )

    assert EscalationState.HUMAN_HANDOFF in states
    # Once handed off, it stays handed off: no later cycle walks it back.
    first_handoff = states.index(EscalationState.HUMAN_HANDOFF)
    assert all(state is EscalationState.HUMAN_HANDOFF for state in states[first_handoff:])
    ledger.verify()
