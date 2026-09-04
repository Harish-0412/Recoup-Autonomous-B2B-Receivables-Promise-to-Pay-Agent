"""SQLAlchemy models for the receivables domain.

Two conventions worth knowing before reading:

* **Business keys, not database keys, are the identifiers.** ``invoice_id`` and
  ``customer_id`` are the strings the synthetic generator, the API and the
  audit ledger all speak. The integer primary keys exist for joins and are
  never exposed.
* **The decision trace is append-only.** ``DecisionTrace`` has no ``updated_at``
  and nothing in this codebase issues an UPDATE against it. Each row carries
  the hash of the row before it, so a silent edit breaks the chain and
  ``app.core.audit.DecisionLedger.verify`` reports where.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import (
    ContactChannel,
    DecisionOutcome,
    DeliveryStatus,
    EscalationState,
    InvoiceStatus,
    PromiseStatus,
    ReplyDisposition,
)
from src.ml.versioning import utc_now


def _enum(python_enum: type, name: str) -> SAEnum:
    """Persist an enum by *value*, so the DB matches the API and the FSM.

    Without ``values_callable`` SQLAlchemy stores member names, which would put
    ``HUMAN_HANDOFF`` in the database while the state machine and every JSON
    response say ``human_handoff``.
    """

    return SAEnum(
        python_enum,
        name=name,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
    )


class Customer(Base):
    """A business that owes money, plus the payment history the scorer reads."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    industry: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    preferred_channel: Mapped[ContactChannel] = mapped_column(
        _enum(ContactChannel, "contact_channel"), default=ContactChannel.EMAIL
    )

    # Observable payment history. These mirror the synthetic generator's
    # Customer fields one-for-one, so a generated batch loads without a mapping
    # layer and the scorer reads identical features in demo and in production.
    tenure_months: Mapped[int] = mapped_column(Integer, default=0)
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_invoice_amount: Mapped[float] = mapped_column(Float, default=0.0)
    on_time_ratio_90d: Mapped[float] = mapped_column(Float, default=0.0)
    on_time_ratio_all_time: Mapped[float] = mapped_column(Float, default=0.0)
    avg_days_late: Mapped[float] = mapped_column(Float, default=0.0)
    prior_broken_promises_count: Mapped[int] = mapped_column(Integer, default=0)
    prior_disputes_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    invoices: Mapped[list[Invoice]] = relationship(back_populates="customer")
    opt_outs: Mapped[list[OptOut]] = relationship(back_populates="customer")


class Invoice(Base):
    """One overdue invoice and the agent's current position on it."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_pk: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)

    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)

    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "invoice_status"), default=InvoiceStatus.OPEN, index=True
    )
    escalation_state: Mapped[EscalationState] = mapped_column(
        _enum(EscalationState, "escalation_state"), default=EscalationState.MONITORING
    )
    #: Position in the configured escalation ladder. Owned by the FSM.
    ladder_index: Mapped[int] = mapped_column(Integer, default=0)

    prior_reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    payment_link_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    payment_link_url: Mapped[str | None] = mapped_column(Text, default=None)

    amount_paid: Mapped[float] = mapped_column(Float, default=0.0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    customer: Mapped[Customer] = relationship(back_populates="invoices")
    promises: Mapped[list[Promise]] = relationship(back_populates="invoice")
    contacts: Mapped[list[ContactLog]] = relationship(back_populates="invoice")


class Promise(Base):
    """A commitment to pay, extracted from a customer reply.

    A promise is evidence of intent, never evidence of payment. Only a verified
    Razorpay webhook moves an invoice to PAID -- see
    ``app.core.promise_tracker``.
    """

    __tablename__ = "promises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promise_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invoice_pk: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)

    promised_amount: Mapped[float] = mapped_column(Float)
    promised_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    #: Which reply this came from, and how confident the classifier was.
    source_reply_id: Mapped[str | None] = mapped_column(String(64), default=None)
    source_confidence: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[PromiseStatus] = mapped_column(
        _enum(PromiseStatus, "promise_status"), default=PromiseStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    invoice: Mapped[Invoice] = relationship(back_populates="promises")


class ContactLog(Base):
    """Every outbound message the agent attempted, and what became of it.

    The contact-frequency cap is enforced by counting rows here, so a message
    that was sent but not logged would silently widen the cap. Executors write
    this row in the same unit of work as the send.

    ``status`` is the column that makes this table honest. A row is written for
    every *attempt*, and only ``SENT`` or ``SIMULATED`` rows are counted as
    contact -- see :func:`app.services.repository.contacts_sent_count`. Before
    this existed, a row was written whether or not anything left the building,
    which meant the caps were being enforced against messages that did not
    exist.
    """

    __tablename__ = "contact_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_pk: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    channel: Mapped[ContactChannel] = mapped_column(_enum(ContactChannel, "contact_channel"))
    ladder_step: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255), default="")
    body_preview: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[DeliveryStatus] = mapped_column(
        _enum(DeliveryStatus, "delivery_status"),
        default=DeliveryStatus.SENT,
        index=True,
    )
    #: The provider's ID for the message, and for the payment link it carried.
    #: Both are how a support question ("did we email them, and what did they
    #: get?") is answered without guessing.
    provider_message_id: Mapped[str | None] = mapped_column(String(128), default=None)
    payment_link_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    #: Why a FAILED attempt failed. Empty on success.
    provider_error: Mapped[str | None] = mapped_column(Text, default=None)

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    @property
    def counts_as_contact(self) -> bool:
        """Whether this attempt should consume the customer's contact budget."""

        return self.status in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED)

    invoice: Mapped[Invoice] = relationship(back_populates="contacts")


class OptOut(Base):
    """A standing instruction not to contact a customer.

    Opt-out is checked as a guard before any send, never as a filter after one.
    """

    __tablename__ = "opt_outs"
    __table_args__ = (
        UniqueConstraint("customer_pk", "channel", name="uq_optout_customer_channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_pk: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    #: NULL means "every channel".
    channel: Mapped[ContactChannel | None] = mapped_column(
        _enum(ContactChannel, "contact_channel"), default=None
    )
    reason: Mapped[str] = mapped_column(String(255), default="")
    source_reply_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    #: NULL means indefinite. A dated opt-out expires per DEFAULT_OPTOUT_DAYS.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    customer: Mapped[Customer] = relationship(back_populates="opt_outs")


class DecisionTrace(Base):
    """Append-only, hash-chained record of every decision the agent made.

    Nothing updates or deletes these rows. ``prev_hash``/``entry_hash`` make an
    after-the-fact edit detectable rather than merely discouraged.
    """

    __tablename__ = "decision_traces"
    __table_args__ = (Index("ix_decision_traces_invoice_seq", "invoice_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Monotonic position in the global ledger.
    seq: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    invoice_id: Mapped[str] = mapped_column(String(64), index=True)

    event: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[DecisionOutcome] = mapped_column(_enum(DecisionOutcome, "decision_outcome"))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64), default="agent")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WebhookEvent(Base):
    """Razorpay webhook deliveries, stored for idempotency.

    Razorpay retries on non-2xx and can deliver the same event more than once.
    Counting a payment twice would corrupt the recovery numbers the whole demo
    is judged on, so the event id is unique and a repeat delivery is a no-op.
    """

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    signature_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    processing_error: Mapped[str | None] = mapped_column(Text, default=None)


class InboundReply(Base):
    """One customer reply, what the classifier made of it, and what followed.

    Every inbound reply lands here, including the ones nothing was done with.
    That is the point: "the agent ignored what the customer said" is exactly
    the accusation this table has to be able to answer, and it can only answer
    it if the unhandled replies are stored too.

    ``disposition`` is the review queue. A reply the classifier was not
    confident about is written with ``NEEDS_REVIEW`` and *no* promise, rather
    than a guessed promise -- a fabricated commitment stops the agent chasing a
    live debt, which is worse than asking a person.
    """

    __tablename__ = "inbound_replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The provider's message id, which is also the idempotency key.
    reply_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    #: Nullable: a reply can arrive that we cannot match to an invoice, and
    #: dropping it would be the one outcome worse than queueing it.
    invoice_pk: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), default=None, index=True
    )
    customer_pk: Mapped[int | None] = mapped_column(
        ForeignKey("customers.id"), default=None, index=True
    )

    from_email: Mapped[str] = mapped_column(String(320), default="")
    to_email: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")

    intent: Mapped[str] = mapped_column(String(64), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classifier_version: Mapped[str] = mapped_column(String(64), default="")
    #: True when the classifier could not run and a fallback answered instead.
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)

    disposition: Mapped[ReplyDisposition] = mapped_column(
        _enum(ReplyDisposition, "reply_disposition"),
        default=ReplyDisposition.NEEDS_REVIEW,
        index=True,
    )
    #: Why it was queued, or what was done with it. Human-readable on purpose:
    #: this is the line a reviewer reads first.
    disposition_reason: Mapped[str] = mapped_column(Text, default="")

    promise_pk: Mapped[int | None] = mapped_column(ForeignKey("promises.id"), default=None)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    @property
    def needs_review(self) -> bool:
        return self.disposition is ReplyDisposition.NEEDS_REVIEW


class BatchRunRecord(Base):
    """Persisted record of an autonomous batch run and its per-invoice decisions.

    Stores both the aggregate RunSummary metrics and the detailed item-level
    decisions (which invoice was used, what decision was made, what the system
    expects next). This powers the live Runs page and audit dashboard.
    """

    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    started_at: Mapped[str] = mapped_column(String(64))
    finished_at: Mapped[str] = mapped_column(String(64), default="")
    ran: Mapped[bool] = mapped_column(Boolean, default=True)
    skipped_reason: Mapped[str] = mapped_column(Text, default="")

    promises_checked: Mapped[int] = mapped_column(Integer, default=0)
    promises_broken: Mapped[int] = mapped_column(Integer, default=0)
    promises_kept: Mapped[int] = mapped_column(Integer, default=0)

    invoices_considered: Mapped[int] = mapped_column(Integer, default=0)
    scored: Mapped[int] = mapped_column(Integer, default=0)
    acted: Mapped[int] = mapped_column(Integer, default=0)
    blocked_by_policy: Mapped[int] = mapped_column(Integer, default=0)
    left_alone: Mapped[int] = mapped_column(Integer, default=0)
    delivery_failed: Mapped[int] = mapped_column(Integer, default=0)
    handed_off: Mapped[int] = mapped_column(Integer, default=0)
    sending_halted: Mapped[bool] = mapped_column(Boolean, default=False)

    errors: Mapped[list[str]] = mapped_column(JSON, default=list)
    invoice_decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
