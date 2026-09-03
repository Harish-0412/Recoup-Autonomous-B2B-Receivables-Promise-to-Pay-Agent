"""Tests for the pyeventsourcing domain aggregate, application, and time-travel replay."""

from datetime import datetime, UTC
import pytest

from app.core.audit import DecisionTraceEntry
from app.core.eventsourcing import (
    InvoiceCaseAggregate,
    RecoupEventApp,
    invoice_id_to_uuid,
)
from app.models.enums import DecisionOutcome


@pytest.fixture
def event_app() -> RecoupEventApp:
    return RecoupEventApp()


def test_open_case(event_app: RecoupEventApp):
    invoice_id = "INV-2026-TEST-01"
    agg_id = event_app.open_case(
        invoice_id=invoice_id,
        customer_id="CUST-ACME",
        amount=150000.0,
        due_date="2026-08-15",
    )
    assert agg_id == invoice_id_to_uuid(invoice_id)
    assert event_app.case_exists(invoice_id)

    case = event_app.get_case(invoice_id)
    assert isinstance(case, InvoiceCaseAggregate)
    assert case.version == 1
    assert case.invoice_id == invoice_id
    assert case.customer_id == "CUST-ACME"
    assert case.original_amount == 150000.0
    assert case.outstanding_amount == 150000.0
    assert case.escalation_state == "monitoring"
    assert case.ladder_index == 0
    assert case.contacts_count == 0
    assert not case.is_paid
    assert not case.is_promised


def test_lifecycle_evaluation_and_intervention(event_app: RecoupEventApp):
    invoice_id = "INV-2026-TEST-02"
    event_app.open_case(invoice_id, "CUST-BETA", 200000.0, "2026-08-01")

    # Evaluation
    case = event_app.record_evaluation(
        invoice_id=invoice_id,
        p_recovery=0.62,
        expected_value=85000.0,
        urgency_tier="REMIND",
        top_drivers=[{"feature": "days_overdue", "shap": 0.35}],
    )
    assert case.version == 2
    assert case.p_recovery == 0.62
    assert case.urgency_tier == "REMIND"

    # Policy Check
    case = event_app.record_policy_check(
        invoice_id=invoice_id,
        allowed=True,
        rule_name="contact_frequency_cap",
        reason="Last contact was 5 days ago",
    )
    assert case.version == 3

    # State transition & Dispatch
    event_app.record_state_transition(invoice_id, "reminder_1", "remind", "1st cycle due")
    case = event_app.record_intervention(
        invoice_id=invoice_id,
        channel="email",
        ladder_step="reminder_1",
        delivery_status="sent",
        payment_link_id="plink_12345",
        cost=25.0,
    )
    assert case.version == 5
    assert case.contacts_count == 1
    assert case.ladder_index == 1
    assert len(case.interventions) == 1
    assert case.interventions[0]["payment_link_id"] == "plink_12345"


def test_promise_lifecycle(event_app: RecoupEventApp):
    invoice_id = "INV-2026-TEST-03"
    event_app.open_case(invoice_id, "CUST-GAMMA", 75000.0, "2026-08-10")

    # Record promise
    case = event_app.record_promise(
        invoice_id=invoice_id,
        promised_amount=75000.0,
        promised_date="2026-09-10",
        confidence=0.92,
    )
    assert case.is_promised
    assert case.escalation_state == "promised"
    assert case.active_promise["promised_date"] == "2026-09-10"
    assert case.active_promise["status"] == "active"

    # Break promise
    case = event_app.record_promise_broken(invoice_id, reason="date_lapsed")
    assert not case.is_promised
    assert case.active_promise["status"] == "broken"


def test_payment_settlement(event_app: RecoupEventApp):
    invoice_id = "INV-2026-TEST-04"
    event_app.open_case(invoice_id, "CUST-DELTA", 100000.0, "2026-08-20")

    # Partial payment
    case = event_app.record_payment(invoice_id, 40000.0, "pay_part1")
    assert case.outstanding_amount == 60000.0
    assert not case.is_paid

    # Full remaining settlement
    case = event_app.record_payment(invoice_id, 60000.0, "pay_part2")
    assert case.outstanding_amount == 0.0
    assert case.is_paid
    assert case.escalation_state == "paid"
    assert case.is_closed


def test_time_travel_replay(event_app: RecoupEventApp):
    invoice_id = "INV-2026-TEST-05"
    event_app.open_case(invoice_id, "CUST-EPSILON", 50000.0, "2026-08-01")  # v1

    event_app.record_evaluation(invoice_id, 0.4, 20000.0, "ESCALATE")  # v2
    event_app.record_intervention(invoice_id, "email", "warning", "sent")  # v3
    event_app.record_promise(invoice_id, 50000.0, "2026-08-25")  # v4
    event_app.record_payment(invoice_id, 50000.0, "pay_full")  # v5

    latest = event_app.get_case(invoice_id)
    assert latest.version == 5
    assert latest.is_paid
    assert latest.outstanding_amount == 0.0

    # Query state at v1 (initial state)
    v1 = event_app.get_case(invoice_id, version=1)
    assert v1.version == 1
    assert v1.outstanding_amount == 50000.0
    assert not v1.is_paid
    assert v1.contacts_count == 0

    # Query state at v3 (after intervention, before promise and payment)
    v3 = event_app.get_case(invoice_id, version=3)
    assert v3.version == 3
    assert v3.contacts_count == 1
    assert not v3.is_promised
    assert not v3.is_paid

    # Query state at v4 (promised, before payment)
    v4 = event_app.get_case(invoice_id, version=4)
    assert v4.version == 4
    assert v4.is_promised
    assert not v4.is_paid

    # Replay timeline
    timeline = event_app.replay_case_history(invoice_id)
    assert len(timeline) == 5
    assert [entry["version"] for entry in timeline] == [1, 2, 3, 4, 5]

    # Project state at version
    proj_v2 = event_app.project_state_at_version(invoice_id, version=2)
    assert proj_v2["p_recovery"] == 0.4
    assert proj_v2["urgency_tier"] == "ESCALATE"


def test_bridge_decision_trace(event_app: RecoupEventApp):
    invoice_id = "INV-2026-BRIDGE-01"

    trace_entry_eval = DecisionTraceEntry(
        seq=0,
        invoice_id=invoice_id,
        event="case_evaluated",
        outcome=DecisionOutcome.APPROVED,
        reason="Urgent overdue case",
        payload={
            "outstanding": 80000.0,
            "customer_id": "CUST-ZETA",
            "p_recovery": 0.55,
            "expected_value": 44000.0,
            "urgency_tier": "REMIND",
        },
        recorded_at=datetime.now(UTC),
    )
    event_app.bridge_decision_trace(trace_entry_eval)

    case = event_app.get_case(invoice_id)
    assert case.original_amount == 80000.0
    assert case.p_recovery == 0.55

    trace_entry_send = DecisionTraceEntry(
        seq=1,
        invoice_id=invoice_id,
        event="intervention_sent",
        outcome=DecisionOutcome.APPROVED,
        reason="Reminder sent",
        payload={
            "channel": "email",
            "ladder_step": "reminder_1",
            "status": "sent",
            "payment_link_id": "plink_bridge",
        },
        recorded_at=datetime.now(UTC),
    )
    event_app.bridge_decision_trace(trace_entry_send)

    updated = event_app.get_case(invoice_id)
    assert updated.contacts_count == 1
    assert updated.interventions[0]["payment_link_id"] == "plink_bridge"
