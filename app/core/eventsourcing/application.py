"""Application service for event-sourced invoice cases.

Provides the event-store repository, aggregate persistence, versioned state
reconstruction, time-travel querying, and bridging with the DecisionLedger.
"""

from __future__ import annotations

import uuid
from typing import Any

from eventsourcing.application import Application

from app.core.audit import DecisionLedger, DecisionTraceEntry
from app.core.domain import CaseSnapshot
from app.core.eventsourcing.domain import InvoiceCaseAggregate
from app.models.enums import DecisionOutcome


def invoice_id_to_uuid(invoice_id: str) -> uuid.UUID:
    """Derive a deterministic UUID from an invoice business ID."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"recoup.invoice.{invoice_id}")


class RecoupEventApp(Application):
    """Event-sourcing application managing InvoiceCaseAggregate lifecycle."""

    def open_case(
        self,
        invoice_id: str,
        customer_id: str,
        amount: float,
        due_date: str,
        currency: str = "INR",
        amount_paid: float = 0.0,
        escalation_state: str = "monitoring",
        ladder_index: int = 0,
    ) -> uuid.UUID:
        """Initialize a new event-sourced case for an invoice."""
        agg_id = invoice_id_to_uuid(invoice_id)
        if agg_id in self.repository:
            return agg_id

        aggregate = InvoiceCaseAggregate(
            id=agg_id,
            invoice_id=invoice_id,
            customer_id=customer_id,
            amount=amount,
            due_date=due_date,
            currency=currency,
            amount_paid=amount_paid,
            escalation_state=escalation_state,
            ladder_index=ladder_index,
        )
        self.save(aggregate)
        return aggregate.id

    def open_case_from_snapshot(self, case: CaseSnapshot) -> uuid.UUID:
        """Open a stream from the exact CaseSnapshot the agent is deciding on."""

        return self.open_case(
            invoice_id=case.invoice.invoice_id,
            customer_id=case.customer.customer_id,
            amount=case.invoice.amount,
            due_date=case.invoice.due_date.isoformat(),
            currency=case.invoice.currency,
            amount_paid=case.invoice.amount_paid,
            escalation_state=case.invoice.escalation_state.value,
            ladder_index=case.invoice.ladder_index,
        )

    def case_exists(self, invoice_id: str) -> bool:
        """Check if an aggregate exists for this invoice ID."""
        return invoice_id_to_uuid(invoice_id) in self.repository

    def get_case(
        self,
        invoice_id: str,
        version: int | None = None,
    ) -> InvoiceCaseAggregate:
        """Retrieve aggregate at latest or specific version (time-travel replay)."""
        agg_id = invoice_id_to_uuid(invoice_id)
        return self.repository.get(agg_id, version=version)

    def record_evaluation(
        self,
        invoice_id: str,
        p_recovery: float,
        expected_value: float,
        urgency_tier: str,
        top_drivers: list[dict[str, Any]] | None = None,
    ) -> InvoiceCaseAggregate:
        """Record ML/rule evaluation on the aggregate."""
        case = self.get_case(invoice_id)
        case.record_evaluation(
            p_recovery=p_recovery,
            expected_value=expected_value,
            urgency_tier=urgency_tier,
            top_drivers=top_drivers,
        )
        self.save(case)
        return case

    def record_policy_check(
        self,
        invoice_id: str,
        allowed: bool,
        rule_name: str | None = None,
        reason: str = "",
    ) -> InvoiceCaseAggregate:
        """Record policy gate evaluation on the aggregate."""
        case = self.get_case(invoice_id)
        case.record_policy_check(allowed=allowed, rule_name=rule_name, reason=reason)
        self.save(case)
        return case

    def record_intervention(
        self,
        invoice_id: str,
        channel: str,
        ladder_step: str,
        delivery_status: str,
        payment_link_id: str | None = None,
        cost: float = 0.0,
    ) -> InvoiceCaseAggregate:
        """Record outbound message dispatch on the aggregate."""
        case = self.get_case(invoice_id)
        case.record_intervention(
            channel=channel,
            ladder_step=ladder_step,
            delivery_status=delivery_status,
            payment_link_id=payment_link_id,
            cost=cost,
        )
        self.save(case)
        return case

    def record_state_transition(
        self,
        invoice_id: str,
        to_state: str,
        trigger: str,
        reason: str = "",
    ) -> InvoiceCaseAggregate:
        """Record state machine transition on the aggregate."""
        case = self.get_case(invoice_id)
        case.transition_state(to_state=to_state, trigger=trigger, reason=reason)
        self.save(case)
        return case

    def record_promise(
        self,
        invoice_id: str,
        promised_amount: float,
        promised_date: str,
        confidence: float = 1.0,
        source: str = "reply",
    ) -> InvoiceCaseAggregate:
        """Record customer promise-to-pay on the aggregate."""
        case = self.get_case(invoice_id)
        case.record_promise(
            promised_amount=promised_amount,
            promised_date=promised_date,
            confidence=confidence,
            source=source,
        )
        self.save(case)
        return case

    def record_promise_broken(
        self,
        invoice_id: str,
        reason: str = "lapsed_unpaid",
    ) -> InvoiceCaseAggregate:
        """Record broken promise on the aggregate."""
        case = self.get_case(invoice_id)
        case.record_promise_broken(reason=reason)
        self.save(case)
        return case

    def record_payment(
        self,
        invoice_id: str,
        amount: float,
        payment_id: str,
        provider: str = "razorpay",
    ) -> InvoiceCaseAggregate:
        """Record webhook payment settlement on the aggregate."""
        case = self.get_case(invoice_id)
        case.record_payment(amount=amount, payment_id=payment_id, provider=provider)
        self.save(case)
        return case

    def record_dispute(
        self,
        invoice_id: str,
        reason: str,
    ) -> InvoiceCaseAggregate:
        """Record customer dispute on the aggregate."""
        case = self.get_case(invoice_id)
        case.raise_dispute(reason=reason)
        self.save(case)
        return case

    def replay_case_history(self, invoice_id: str) -> list[dict[str, Any]]:
        """Replay and export all domain events for an invoice."""
        case = self.get_case(invoice_id)
        events = []
        for v in range(1, case.version + 1):
            historical = self.get_case(invoice_id, version=v)
            events.append(
                {
                    "version": v,
                    "escalation_state": historical.escalation_state,
                    "ladder_index": historical.ladder_index,
                    "contacts_count": historical.contacts_count,
                    "outstanding_amount": historical.outstanding_amount,
                    "is_promised": historical.is_promised,
                    "is_paid": historical.is_paid,
                    "is_disputed": historical.is_disputed,
                }
            )
        return events

    def project_state_at_version(self, invoice_id: str, version: int) -> dict[str, Any]:
        """Project the state of an invoice as it existed at `version`."""
        historical = self.get_case(invoice_id, version=version)
        return {
            "invoice_id": historical.invoice_id,
            "customer_id": historical.customer_id,
            "version": historical.version,
            "escalation_state": historical.escalation_state,
            "ladder_index": historical.ladder_index,
            "contacts_count": historical.contacts_count,
            "outstanding_amount": historical.outstanding_amount,
            "original_amount": historical.original_amount,
            "p_recovery": historical.p_recovery,
            "expected_value": historical.expected_value,
            "urgency_tier": historical.urgency_tier,
            "is_promised": historical.is_promised,
            "active_promise": historical.active_promise,
            "is_paid": historical.is_paid,
            "is_disputed": historical.is_disputed,
            "is_closed": historical.is_closed,
        }

    def bridge_decision_trace(self, entry: DecisionTraceEntry) -> None:
        """Bridge a DecisionTraceEntry into event-sourced aggregate events."""
        invoice_id = entry.invoice_id
        if not self.case_exists(invoice_id):
            payload = entry.payload
            amount = float(payload.get("outstanding") or payload.get("amount") or 10000.0)
            customer_id = str(payload.get("customer_id") or "CUST-UNKNOWN")
            due_date = str(payload.get("due_date") or "2026-09-01")
            self.open_case(
                invoice_id,
                customer_id,
                amount,
                due_date,
                currency=str(payload.get("currency") or "INR"),
                amount_paid=float(payload.get("amount_paid", 0.0)),
                escalation_state=str(payload.get("escalation_state") or "monitoring"),
                ladder_index=int(payload.get("ladder_index", 0)),
            )

        event_name = entry.event.lower()
        payload = entry.payload

        if "score" in event_name or "evaluated" in event_name:
            self.record_evaluation(
                invoice_id=invoice_id,
                p_recovery=float(payload.get("p_recovery", 0.5)),
                expected_value=float(payload.get("expected_value", 0.0)),
                urgency_tier=str(payload.get("urgency_tier", "WAIT")),
                top_drivers=payload.get("top_drivers"),
            )
        elif "policy" in event_name or "gate" in event_name:
            self.record_policy_check(
                invoice_id=invoice_id,
                allowed=entry.outcome == DecisionOutcome.APPROVED,
                rule_name=str(payload.get("rule", "")),
                reason=entry.reason,
            )
        elif event_name.startswith("transition:"):
            self.record_state_transition(
                invoice_id=invoice_id,
                to_state=str(payload.get("to_state") or payload.get("state_after") or "monitoring"),
                trigger=str(payload.get("trigger") or event_name.removeprefix("transition:")),
                reason=entry.reason,
            )
        elif any(k in event_name for k in ("execute", "sent", "send", "contact", "intervention")):
            self.record_intervention(
                invoice_id=invoice_id,
                channel=str(payload.get("channel", "email")),
                ladder_step=str(payload.get("ladder_step", "reminder_1")),
                delivery_status=str(payload.get("status", "sent")),
                payment_link_id=payload.get("payment_link_id"),
                cost=float(payload.get("cost", 0.0)),
            )
        elif "promise" in event_name:
            if "broken" in event_name:
                self.record_promise_broken(invoice_id, reason=entry.reason)
            else:
                self.record_promise(
                    invoice_id=invoice_id,
                    promised_amount=float(payload.get("amount", 0.0)),
                    promised_date=str(payload.get("date", "")),
                    confidence=float(payload.get("confidence", 1.0)),
                )
        elif "payment" in event_name or "paid" in event_name:
            self.record_payment(
                invoice_id=invoice_id,
                amount=float(payload.get("amount", 0.0)),
                payment_id=str(payload.get("payment_id", "pay_manual")),
            )


class EventSourcedDecisionLedger(DecisionLedger):
    """Decision ledger that mirrors every append into ``RecoupEventApp``.

    API and batch-run paths use this during the dual-write window. Existing
    unit tests and pure scoring helpers can keep using ``DecisionLedger`` when
    they need no durable aggregate stream.
    """

    def __init__(self, event_app: RecoupEventApp | None = None) -> None:
        super().__init__()
        self.event_app = event_app or RecoupEventApp()
        self._opened_invoice_ids: set[str] = set()
        self.event_versions_by_hash: dict[str, int] = {}

    def open_case_from_snapshot(self, case: CaseSnapshot) -> None:
        self.event_app.open_case_from_snapshot(case)
        self._opened_invoice_ids.add(case.invoice_id)

    @property
    def opened_invoice_ids(self) -> frozenset[str]:
        return frozenset(self._opened_invoice_ids)

    def append(self, **kwargs: Any) -> DecisionTraceEntry:
        entry = super().append(**kwargs)
        self.event_app.bridge_decision_trace(entry)
        self.event_versions_by_hash[entry.entry_hash] = self.event_app.get_case(
            entry.invoice_id
        ).version
        return entry
