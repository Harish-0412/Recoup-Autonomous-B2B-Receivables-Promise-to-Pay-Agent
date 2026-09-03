"""Shared fixtures for the core tests.

Everything here builds plain in-memory objects. No database, no network, no
API keys -- the safety properties these tests assert must be verifiable on a
clean checkout, or they are not properties anyone can check.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.audit import DecisionLedger
from app.core.domain import CaseSnapshot, CustomerSnapshot, InvoiceSnapshot
from app.core.policy import PolicyConfig, PolicyEngine
from app.models.enums import ContactChannel

AS_OF = date(2026, 9, 1)


def make_case(
    *,
    invoice_id: str = "INV-1",
    customer_id: str = "CUST-1",
    amount: float = 100_000.0,
    days_overdue: int = 20,
    days_since_last_contact: int = 9_999,
    prior_reminders_sent: int = 0,
    ladder_index: int = 0,
    on_time_ratio: float = 0.6,
    avg_days_late: float = 8.0,
    broken_promises: int = 0,
    invoice_count: int = 20,
    opted_out: frozenset[ContactChannel | None] = frozenset(),
    has_open_promise: bool = False,
    open_promise_due_in_days: int | None = None,
    **invoice_overrides: object,
) -> CaseSnapshot:
    """Build a case snapshot with sensible, overridable defaults."""

    invoice_fields: dict[str, object] = {
        "invoice_id": invoice_id,
        "customer_id": customer_id,
        "amount": amount,
        "issue_date": AS_OF - date.resolution * (days_overdue + 30),
        "due_date": AS_OF - date.resolution * days_overdue,
        "as_of": AS_OF,
        "days_overdue": days_overdue,
        "days_since_last_contact": days_since_last_contact,
        "prior_reminders_sent": prior_reminders_sent,
        "ladder_index": ladder_index,
        "has_open_promise": has_open_promise,
        "open_promise_due_in_days": open_promise_due_in_days,
    }
    invoice_fields.update(invoice_overrides)

    return CaseSnapshot(
        invoice=InvoiceSnapshot(**invoice_fields),  # type: ignore[arg-type]
        customer=CustomerSnapshot(
            customer_id=customer_id,
            name="Test Traders Pvt Ltd",
            invoice_count=invoice_count,
            avg_invoice_amount=amount,
            on_time_ratio_90d=on_time_ratio,
            on_time_ratio_all_time=on_time_ratio,
            avg_days_late=avg_days_late,
            prior_broken_promises_count=broken_promises,
        ),
        opted_out_channels=opted_out,
    )


@pytest.fixture
def ledger() -> DecisionLedger:
    """A fresh ledger per test, never the process-wide default."""

    return DecisionLedger()


@pytest.fixture
def policy_config() -> PolicyConfig:
    return PolicyConfig(
        discount_ceiling_pct=10.0,
        min_contact_gap_days=3,
        max_contacts_per_invoice=4,
    )


@pytest.fixture
def engine(policy_config: PolicyConfig, ledger: DecisionLedger) -> PolicyEngine:
    return PolicyEngine(policy_config, ledger=ledger)
