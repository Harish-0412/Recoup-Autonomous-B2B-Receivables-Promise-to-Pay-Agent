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
* **Wave 1 tenancy: every tenant table carries ``business_id``.** One Postgres,
  many businesses; every read/write is filtered by ``business_id``. Business
  keys are unique *within* a business -- two tenants can both have INV-1042 --
  so uniqueness is always composite ``(business_id, <key>)``. See
  docs/tenancy.md.
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
    AllocationSource,
    ContactChannel,
    DecisionOutcome,
    DeliveryStatus,
    EscalationState,
    IntegrationProvider,
    InvoiceStatus,
    PromiseStatus,
    ReplyDisposition,
)
from src.ml.versioning import utc_now

#: Owner key used when a row predates tenancy or a caller has not resolved one.
#: Matches the ``server_default='default'`` in the Wave 1 migration and
#: ``Settings.BUSINESS_ID``. New code must always pass an explicit business_id;
#: this exists only so legacy rows and NOT NULL stay compatible.
DEFAULT_BUSINESS_ID: str = "default"


def _business_id_column() -> Mapped[str]:
    return mapped_column(
        Text, nullable=False, default=DEFAULT_BUSINESS_ID, server_default=DEFAULT_BUSINESS_ID
    )


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


class Business(Base):
    """One tenant in a shared database. Not in the agent loop.

    v1 resolution is API_KEY -> business_id via ``api_key`` / ``task_api_key``
    columns (later JWT ``org_id``). ``business_id`` is the stable owner key
    recorded on every tenant row; it is what the Wave 1 migration backfills
    from ``Settings.BUSINESS_ID``.
    """

    __tablename__ = "businesses"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_businesses_business_id"),
        UniqueConstraint("api_key", name="uq_businesses_api_key"),
        UniqueConstraint("task_api_key", name="uq_businesses_task_api_key"),
        Index("ix_businesses_business_id", "business_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(255), default="")
    #: Operator credential for dashboard routes (maps to Settings.API_KEY on
    #: the default business). Stored verbatim in v1; rotate via update.
    api_key: Mapped[str | None] = mapped_column(Text, default=None)
    #: Cron credential for task routes (maps to Settings.TASK_API_KEY).
    task_api_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Customer(Base):
    """A business that owes money, plus the payment history the scorer reads."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("business_id", "customer_id", name="uq_customers_business_customer"),
        Index("ix_customers_business_id", "business_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
    customer_id: Mapped[str] = mapped_column(String(64))
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
    drift_flags: Mapped[list[CustomerDriftFlag]] = relationship(back_populates="customer")


class Invoice(Base):
    """One overdue invoice and the agent's current position on it."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("business_id", "invoice_id", name="uq_invoices_business_invoice"),
        Index("ix_invoices_business_status_due_date", "business_id", "status", "due_date"),
        Index("ix_invoices_business_payment_link_id", "business_id", "payment_link_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
    invoice_id: Mapped[str] = mapped_column(String(64))
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

    #: Opaque external IDs from ERP systems, keyed by provider name.
    #: e.g. {"zoho_invoice_id": "INV-00042", "qbo_invoice_id": "123"}
    #: Never overwritten by Recoup — ERP owns issued amount and due date;
    #: Recoup owns collections state.
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Which system originated this invoice. "batch_ingest" for demo data;
    #: "zoho_books", "quickbooks", "razorpay_invoices", "tally" for production.
    erp_source: Mapped[str] = mapped_column(Text, default="batch_ingest")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    customer: Mapped[Customer] = relationship(back_populates="invoices")
    promises: Mapped[list[Promise]] = relationship(back_populates="invoice")
    contacts: Mapped[list[ContactLog]] = relationship(back_populates="invoice")
    allocations: Mapped[list[PaymentAllocation]] = relationship(back_populates="invoice")


class Promise(Base):
    """A commitment to pay, extracted from a customer reply.

    A promise is evidence of intent, never evidence of payment. Only a verified
    Razorpay webhook moves an invoice to PAID -- see
    ``app.core.promise_tracker``.
    """

    __tablename__ = "promises"
    __table_args__ = (
        UniqueConstraint("business_id", "promise_id", name="uq_promises_business_promise"),
        Index("ix_promises_business_status", "business_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
    promise_id: Mapped[str] = mapped_column(String(64))
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
    broken_promise_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

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
    __table_args__ = (Index("ix_contact_logs_business_invoice_pk", "business_id", "invoice_pk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
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
    #: The bandit arm this contact was sent in (e.g. "tue_midday"), and the
    #: recommended send time. Null when the timing model was unavailable: the
    #: arm is how a later reply attributes its reward to the slot that earned
    #: it, which is what keeps the bandit learning from genuine responses.
    timing_arm: Mapped[str | None] = mapped_column(String(32), default=None)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

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
        Index("ix_opt_outs_business_customer_pk", "business_id", "customer_pk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
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
    __table_args__ = (
        Index("ix_decision_traces_invoice_seq", "invoice_id", "seq"),
        Index("ix_decision_traces_business_invoice", "business_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
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


class RecoupEventRecord(Base):
    """Durable event-store stream for event-sourced invoice aggregates.

    ``DecisionTrace`` remains the hash-chained migration ledger. This table is
    the event-store side of the dual-write: each row names the aggregate
    version produced by replaying one domain/audit event and stores the
    projected aggregate state at that version so replay checks can compare the
    event stream with the ORM row.
    """

    __tablename__ = "recoup_event_records"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "invoice_id",
            "version",
            name="uq_recoup_events_business_invoice_version",
        ),
        UniqueConstraint("trace_hash", name="uq_recoup_events_trace_hash"),
        Index("ix_recoup_events_business_invoice_version", "business_id", "invoice_id", "version"),
        Index("ix_recoup_events_business_recorded_at", "business_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    invoice_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)

    #: ``case:opened`` rows have no DecisionTrace counterpart, so trace fields
    #: are nullable. All hot-path decision events carry the hash-chain pointer.
    trace_seq: Mapped[int | None] = mapped_column(Integer, default=None)
    trace_hash: Mapped[str | None] = mapped_column(String(64), default=None)

    event: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[DecisionOutcome | None] = mapped_column(
        _enum(DecisionOutcome, "decision_outcome"), default=None
    )
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64), default="agent")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    aggregate_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WebhookEvent(Base):
    """Razorpay webhook deliveries, stored for idempotency.

    Razorpay retries on non-2xx and can deliver the same event more than once.
    Counting a payment twice would corrupt the recovery numbers the whole demo
    is judged on, so the event id is unique per business and a repeat delivery
    is a no-op.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("business_id", "event_id", name="uq_webhook_events_business_event"),
        Index("ix_webhook_events_business_received_at", "business_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
    event_id: Mapped[str] = mapped_column(String(128))
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
    __table_args__ = (
        Index("ix_inbound_replies_business_customer_pk", "business_id", "customer_pk"),
        Index("ix_inbound_replies_business_received_at", "business_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
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
    __table_args__ = (Index("ix_batch_runs_business_created_at", "business_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
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


class CustomerDriftFlag(Base):
    """One nightly drift verdict for one customer.

    Written by ``scripts/run_drift_detection.py``, read by ``GET
    /api/v1/drift/flags``. A flag is a suggestion that a human look at the
    customer -- it never changes invoice state, freezes escalation, or
    sends anything. ``flagged=False`` rows are kept too, so "was this
    customer checked, and what did the model say" is answerable later.
    """

    __tablename__ = "customer_drift_flags"
    __table_args__ = (
        Index("ix_customer_drift_flags_business_customer_pk", "business_id", "customer_pk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()
    customer_pk: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)

    anomaly_score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    model_version: Mapped[str] = mapped_column(String(64), default="")
    #: Trailing window the verdict was computed over, in days.
    window_days: Mapped[int] = mapped_column(Integer, default=90)
    #: Feature values and top drivers, for the reviewer who opens the flag.
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    customer: Mapped[Customer] = relationship(back_populates="drift_flags")


class PaymentAllocation(Base):
    """One unit of settled money, from any source.

    This table is the single source of truth for "how much has been paid
    against this invoice". ``invoices.amount_paid`` is always derived from
    ``SUM(payment_allocations.amount)`` for the invoice's pk -- it is never
    incremented directly. That design means:

    * ``payment.captured`` and ``payment_link.paid`` (which can both fire for
      the same Razorpay transaction) are keyed by ``(business_id, source,
      provider_ref)``. The unique constraint turns the second event into a
      DB-level no-op rather than a double-count.
    * Operator-entered UTRs and ERP credit notes live in the same table, so
      a single SUM computes the correct outstanding balance regardless of how
      money arrived.
    * ``invoice_pk`` is nullable: a bank UTR enters unmatched (NULL) and is
      linked to an invoice only after a human confirms the match.

    The ``amount`` column is signed -- negative for credit notes.
    """

    __tablename__ = "payment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "source",
            "provider_ref",
            name="uq_payment_allocations_business_source_ref",
        ),
        Index("ix_payment_allocations_business_invoice_pk", "business_id", "invoice_pk"),
        Index("ix_payment_allocations_business_source", "business_id", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()

    #: FK to the invoice this payment settles. NULL until a human confirms the
    #: match for bank UTRs; always set for Razorpay webhooks and ERP allocations.
    invoice_pk: Mapped[int | None] = mapped_column(
        ForeignKey("invoices.id"), default=None, index=True
    )

    #: What kind of payment event this is.
    source: Mapped[AllocationSource] = mapped_column(
        _enum(AllocationSource, "allocation_source"), index=True
    )
    #: The provider's opaque reference: Razorpay payment_id, UTR string, or
    #: ERP credit note ID.
    provider_ref: Mapped[str] = mapped_column(String(128))

    #: Positive for receipts, negative for credit notes.
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="INR")

    #: When the money actually cleared (bank value date / Razorpay captured_at).
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    #: Who wrote this row: "webhook", "operator", "erp_sync".
    recorded_by: Mapped[str] = mapped_column(String(32), default="webhook")

    #: Payer account hint (IFSC/account number or ERP customer_id), if known.
    payer_account: Mapped[str | None] = mapped_column(String(128), default=None)

    #: Operator / sync notes, for the review queue.
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    invoice: Mapped[Invoice | None] = relationship(back_populates="allocations")


class IntegrationCredential(Base):
    """OAuth2 / API credentials for one ERP provider, one tenant.

    One row per (business_id, provider). Recoup stores the refresh token
    verbatim in v1; Wave 3 will encrypt it using the secrets manager before
    writing. Never log or expose the token columns.

    The ``extra`` JSON bag holds provider-specific state:
    * Zoho: ``org_id``, ``zoho_account_domain``
    * QuickBooks: ``realm_id`` (Intuit company ID), ``environment``
    * Razorpay Invoices: empty (uses global RAZORPAY_KEY_ID / SECRET)
    * Tally: ``last_import_filename``, ``last_import_rows``
    """

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "provider",
            name="uq_integration_credentials_business_provider",
        ),
        Index("ix_integration_credentials_business_id", "business_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[str] = _business_id_column()

    provider: Mapped[IntegrationProvider] = mapped_column(
        _enum(IntegrationProvider, "integration_provider"), index=True
    )

    #: Current short-lived access token. May be empty/expired; always refresh
    #: before use.
    access_token: Mapped[str] = mapped_column(Text, default="")
    #: Long-lived token used to mint new access tokens.
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    #: Space-separated OAuth scopes granted.
    scope: Mapped[str] = mapped_column(Text, default="")

    #: Provider-specific metadata (org_id, realm_id, etc.).
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_sync_invoices: Mapped[int] = mapped_column(Integer, default=0)
    last_sync_errors: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
