"""Domain aggregate and events for event-sourced invoice recovery cases.

Built on ``eventsourcing.domain.Aggregate`` to provide:
1. Complete event-driven lifecycle tracking for every overdue invoice.
2. Tamper-evident, reproducible state reconstruction from domain events.
3. Time-travel replay: querying an invoice's state at any historical version.
"""

from __future__ import annotations

import uuid
from typing import Any
from eventsourcing.domain import Aggregate, event


class InvoiceCaseAggregate(Aggregate):
    """Event-sourced aggregate root representing a collections case."""

    @event("Opened")
    def __init__(
        self,
        id: uuid.UUID,
        invoice_id: str,
        customer_id: str,
        amount: float,
        due_date: str,
        currency: str = "INR",
    ) -> None:
        self.invoice_id = invoice_id
        self.customer_id = customer_id
        self.original_amount = float(amount)
        self.outstanding_amount = float(amount)
        self.currency = currency
        self.due_date = due_date
        self.escalation_state = "monitoring"
        self.ladder_index = 0
        self.contacts_count = 0
        self.p_recovery = 0.0
        self.expected_value = 0.0
        self.urgency_tier = "WAIT"
        self.is_promised = False
        self.active_promise: dict[str, Any] | None = None
        self.is_paid = False
        self.is_disputed = False
        self.is_closed = False
        self.interventions: list[dict[str, Any]] = []
        self.decision_log: list[dict[str, Any]] = []

    @event("Evaluated")
    def record_evaluation(
        self,
        p_recovery: float,
        expected_value: float,
        urgency_tier: str,
        top_drivers: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record model/scorer expected value & recovery probability evaluation."""
        self.p_recovery = float(p_recovery)
        self.expected_value = float(expected_value)
        self.urgency_tier = urgency_tier
        self.decision_log.append(
            {
                "event": "evaluated",
                "p_recovery": self.p_recovery,
                "expected_value": self.expected_value,
                "urgency_tier": self.urgency_tier,
                "top_drivers": top_drivers or [],
            }
        )

    @event("PolicyEvaluated")
    def record_policy_check(
        self,
        allowed: bool,
        rule_name: str | None = None,
        reason: str = "",
    ) -> None:
        """Record policy gate enforcement."""
        self.decision_log.append(
            {
                "event": "policy_evaluated",
                "allowed": allowed,
                "rule_name": rule_name,
                "reason": reason,
            }
        )

    @event("InterventionDispatched")
    def record_intervention(
        self,
        channel: str,
        ladder_step: str,
        delivery_status: str,
        payment_link_id: str | None = None,
        cost: float = 0.0,
    ) -> None:
        """Record an outbound reminder/notice dispatch."""
        self.contacts_count += 1
        self.ladder_index += 1
        intervention_record = {
            "channel": channel,
            "ladder_step": ladder_step,
            "delivery_status": delivery_status,
            "payment_link_id": payment_link_id,
            "cost": cost,
        }
        self.interventions.append(intervention_record)
        self.decision_log.append({"event": "intervention_dispatched", **intervention_record})

    @event("StateTransitioned")
    def transition_state(
        self,
        to_state: str,
        trigger: str,
        reason: str = "",
    ) -> None:
        """Record state machine escalation transitions."""
        self.escalation_state = to_state
        self.decision_log.append(
            {
                "event": "state_transitioned",
                "to_state": to_state,
                "trigger": trigger,
                "reason": reason,
            }
        )

    @event("PromiseRecorded")
    def record_promise(
        self,
        promised_amount: float,
        promised_date: str,
        confidence: float = 1.0,
        source: str = "reply",
    ) -> None:
        """Record an inbound customer promise-to-pay."""
        self.is_promised = True
        self.escalation_state = "promised"
        self.active_promise = {
            "promised_amount": float(promised_amount),
            "promised_date": promised_date,
            "confidence": float(confidence),
            "source": source,
            "status": "active",
        }
        self.decision_log.append({"event": "promise_recorded", **self.active_promise})

    @event("PromiseBroken")
    def record_promise_broken(self, reason: str = "lapsed_unpaid") -> None:
        """Mark promise as broken and resume escalation eligibility."""
        self.is_promised = False
        if self.active_promise:
            self.active_promise["status"] = "broken"
        self.decision_log.append({"event": "promise_broken", "reason": reason})

    @event("PromiseKept")
    def record_promise_kept(self) -> None:
        """Mark promise as fulfilled."""
        self.is_promised = False
        if self.active_promise:
            self.active_promise["status"] = "kept"
        self.decision_log.append({"event": "promise_kept"})

    @event("PaymentSettled")
    def record_payment(
        self,
        amount: float,
        payment_id: str,
        provider: str = "razorpay",
    ) -> None:
        """Record verified payment from provider webhook."""
        paid_amount = float(amount)
        self.outstanding_amount = max(0.0, self.outstanding_amount - paid_amount)
        if self.outstanding_amount == 0.0:
            self.is_paid = True
            self.escalation_state = "paid"
            self.is_closed = True
            if self.is_promised and self.active_promise:
                self.is_promised = False
                self.active_promise["status"] = "kept"
        self.decision_log.append(
            {
                "event": "payment_settled",
                "amount": paid_amount,
                "payment_id": payment_id,
                "provider": provider,
                "remaining_outstanding": self.outstanding_amount,
            }
        )

    @event("DisputeRaised")
    def raise_dispute(self, reason: str) -> None:
        """Record a formal dispute and pause collection."""
        self.is_disputed = True
        self.escalation_state = "disputed"
        self.decision_log.append({"event": "dispute_raised", "reason": reason})

    @event("Closed")
    def close_case(self, final_status: str, reason: str = "") -> None:
        """Close case (e.g. human handoff, written off, settled)."""
        self.is_closed = True
        self.escalation_state = final_status
        self.decision_log.append(
            {"event": "closed", "final_status": final_status, "reason": reason}
        )
