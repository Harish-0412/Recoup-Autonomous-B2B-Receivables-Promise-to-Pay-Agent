"""The read models the agent's core logic operates on.

Why this layer exists at all: the scorer, the policy engine and the escalation
machine must be runnable with no database. ``scripts/run_batch_demo.py`` has to
work on a laptop with nothing but Python installed, and the unit tests that
prove the safety guarantees must not depend on Postgres being up. So the core
takes plain Pydantic snapshots, and the two places that *have* real data --
the synthetic generator and the ORM -- each supply a small adapter.

The snapshots are deliberately a subset. They carry what a decision needs and
nothing else; ground-truth fields such as the generator's ``recovered`` label
have no adapter into this layer, so the agent cannot accidentally read the
answer it is supposed to be predicting.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ContactChannel, EscalationState, InvoiceStatus

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, types only
    from app.models.tables import Customer as CustomerRow
    from app.models.tables import Invoice as InvoiceRow


class CustomerSnapshot(BaseModel):
    """A customer's observable payment behaviour, as of a scoring instant."""

    model_config = ConfigDict(protected_namespaces=())

    customer_id: str = Field(min_length=1)
    name: str = ""
    industry: str = ""
    email: str | None = None
    preferred_channel: ContactChannel = ContactChannel.EMAIL

    tenure_months: int = Field(default=0, ge=0)
    invoice_count: int = Field(default=0, ge=0)
    avg_invoice_amount: float = Field(default=0.0, ge=0.0)
    on_time_ratio_90d: float = Field(default=0.0, ge=0.0, le=1.0)
    on_time_ratio_all_time: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_days_late: float = Field(default=0.0, ge=0.0)
    prior_broken_promises_count: int = Field(default=0, ge=0)
    prior_disputes_count: int = Field(default=0, ge=0)

    @property
    def broken_promise_rate(self) -> float:
        """Broken promises as a *rate*, not a count.

        Counts grow with tenure, so scoring on them punishes long-standing
        customers for being long-standing. The synthetic generator's outcome
        model uses the same normalisation, which is what keeps the rules-based
        scorer honest against the data it is evaluated on.
        """

        return min(self.prior_broken_promises_count / max(self.invoice_count, 1), 1.0)

    @property
    def dispute_rate(self) -> float:
        """Disputes as a rate, normalised the same way as broken promises."""

        return min(self.prior_disputes_count / max(self.invoice_count, 1), 1.0)


class InvoiceSnapshot(BaseModel):
    """One overdue invoice at the moment the agent is deciding about it."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    amount: float = Field(gt=0)
    currency: str = "INR"
    issue_date: date
    due_date: date
    payment_terms_days: int = Field(default=30, gt=0)

    #: The "as of" instant. Every field below is true as of this date.
    as_of: date
    days_overdue: int = Field(ge=0)

    status: InvoiceStatus = InvoiceStatus.OPEN
    escalation_state: EscalationState = EscalationState.MONITORING
    ladder_index: int = Field(default=0, ge=0)

    prior_reminders_sent: int = Field(default=0, ge=0)
    #: Large sentinel when the customer has never been contacted, so that a
    #: "minimum gap since last contact" rule passes for a first contact rather
    #: than blocking it -- zero would read as "contacted today".
    days_since_last_contact: int = Field(default=9_999, ge=0)

    has_prior_promise: bool = False
    prior_promise_kept: bool | None = None
    has_open_promise: bool = False
    open_promise_due_in_days: int | None = None

    amount_paid: float = Field(default=0.0, ge=0.0)

    @property
    def outstanding(self) -> float:
        """What is still owed, never below zero."""

        return max(self.amount - self.amount_paid, 0.0)

    @property
    def is_actionable(self) -> bool:
        """Whether this invoice is still a candidate for agent action."""

        return self.status in {
            InvoiceStatus.OPEN,
            InvoiceStatus.IN_PROGRESS,
            InvoiceStatus.PROMISED,
        }


class CaseSnapshot(BaseModel):
    """An invoice paired with its customer -- the unit a decision is made on."""

    model_config = ConfigDict(protected_namespaces=())

    invoice: InvoiceSnapshot
    customer: CustomerSnapshot
    #: Channels this customer has opted out of. An empty set means no opt-out;
    #: ``None`` in the set means "all channels".
    opted_out_channels: frozenset[ContactChannel | None] = frozenset()

    @property
    def invoice_id(self) -> str:
        return self.invoice.invoice_id

    def is_opted_out(self, channel: ContactChannel | None = None) -> bool:
        """Whether contact is barred, on this channel or on every channel."""

        if None in self.opted_out_channels:
            return True
        if channel is None:
            return bool(self.opted_out_channels)
        return channel in self.opted_out_channels


def _days_between(later: date, earlier: date) -> int:
    return max((later - earlier).days, 0)


def snapshot_from_generated(
    invoice: Any,
    customer: Any,
    *,
    as_of: date | None = None,
) -> CaseSnapshot:
    """Adapt one ``src.data.synthetic_generator`` pair into a ``CaseSnapshot``.

    Typed loosely on purpose: importing the generator's Pydantic models here
    would make ``app`` depend on ``src.data``, and the demo script is the only
    caller that has those objects in hand.

    Ground-truth fields (``recovered``, ``true_recovery_probability``,
    ``eventual_payment_days``) are not read. That is the leakage guarantee this
    adapter is responsible for.
    """

    reference = as_of or invoice.flagged_date

    return CaseSnapshot(
        invoice=InvoiceSnapshot(
            invoice_id=invoice.invoice_id,
            customer_id=invoice.customer_id,
            amount=float(invoice.amount),
            currency=getattr(invoice, "currency", "INR"),
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            payment_terms_days=int(invoice.payment_terms_days),
            as_of=reference,
            days_overdue=int(invoice.days_overdue_at_flag),
            ladder_index=int(invoice.current_escalation_tier),
            escalation_state=(
                EscalationState.MONITORING
                if invoice.prior_reminders_sent == 0
                else EscalationState.REMINDED
            ),
            prior_reminders_sent=int(invoice.prior_reminders_sent),
            days_since_last_contact=int(invoice.days_since_last_contact),
            has_prior_promise=bool(invoice.has_prior_promise),
            prior_promise_kept=invoice.prior_promise_kept,
        ),
        customer=CustomerSnapshot(
            customer_id=customer.customer_id,
            name=customer.name,
            industry=customer.industry,
            preferred_channel=(
                ContactChannel.WHATSAPP
                if str(customer.preferred_channel).lower() == "whatsapp"
                else ContactChannel.EMAIL
            ),
            tenure_months=int(customer.tenure_months),
            invoice_count=int(customer.invoice_count),
            avg_invoice_amount=float(customer.avg_invoice_amount),
            on_time_ratio_90d=float(customer.on_time_ratio_90d),
            on_time_ratio_all_time=float(customer.on_time_ratio_all_time),
            avg_days_late=float(customer.avg_days_late),
            prior_broken_promises_count=int(customer.prior_broken_promises_count),
            prior_disputes_count=int(customer.prior_disputes_count),
        ),
    )


def snapshot_from_rows(
    invoice: InvoiceRow,
    customer: CustomerRow,
    *,
    as_of: date | None = None,
    now: datetime | None = None,
    opted_out_channels: frozenset[ContactChannel | None] = frozenset(),
    has_open_promise: bool = False,
    open_promise_due_in_days: int | None = None,
) -> CaseSnapshot:
    """Adapt persisted rows into the same ``CaseSnapshot`` the demo uses.

    The API path and the batch-demo path converge here, which is the point:
    one scorer, one policy engine, one set of tests, two data sources.
    """

    reference = as_of or (now.date() if now else date.today())
    if invoice.last_contact_at is None:
        days_since_contact = 9_999
    else:
        days_since_contact = _days_between(reference, invoice.last_contact_at.date())

    return CaseSnapshot(
        invoice=InvoiceSnapshot(
            invoice_id=invoice.invoice_id,
            customer_id=customer.customer_id,
            amount=float(invoice.amount),
            currency=invoice.currency,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            payment_terms_days=invoice.payment_terms_days,
            as_of=reference,
            days_overdue=_days_between(reference, invoice.due_date),
            status=invoice.status,
            escalation_state=invoice.escalation_state,
            ladder_index=invoice.ladder_index,
            prior_reminders_sent=invoice.prior_reminders_sent,
            days_since_last_contact=days_since_contact,
            has_open_promise=has_open_promise,
            open_promise_due_in_days=open_promise_due_in_days,
            amount_paid=float(invoice.amount_paid),
        ),
        customer=CustomerSnapshot(
            customer_id=customer.customer_id,
            name=customer.name,
            industry=customer.industry,
            email=customer.email,
            preferred_channel=customer.preferred_channel,
            tenure_months=customer.tenure_months,
            invoice_count=customer.invoice_count,
            avg_invoice_amount=float(customer.avg_invoice_amount),
            on_time_ratio_90d=customer.on_time_ratio_90d,
            on_time_ratio_all_time=customer.on_time_ratio_all_time,
            avg_days_late=customer.avg_days_late,
            prior_broken_promises_count=customer.prior_broken_promises_count,
            prior_disputes_count=customer.prior_disputes_count,
        ),
        opted_out_channels=opted_out_channels,
    )
