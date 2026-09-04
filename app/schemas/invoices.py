"""Request and response contracts for the invoice endpoints.

Separate from both the ORM models and the core snapshots on purpose. The ORM
shape is a storage decision, the snapshot is what a decision needs, and this is
what the outside world is promised -- coupling them would mean a column rename
becomes a breaking API change.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ContactChannel,
    EscalationState,
    InterventionTier,
    InvoiceStatus,
    PromiseStatus,
)


class CustomerIn(BaseModel):
    """One customer in an ingest batch."""

    model_config = ConfigDict(protected_namespaces=())

    customer_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    industry: str = ""
    email: str | None = None
    phone: str | None = None
    preferred_channel: ContactChannel = ContactChannel.EMAIL

    tenure_months: int = Field(default=0, ge=0)
    invoice_count: int = Field(default=0, ge=0)
    avg_invoice_amount: float = Field(default=0.0, ge=0.0)
    on_time_ratio_90d: float = Field(default=0.0, ge=0.0, le=1.0)
    on_time_ratio_all_time: float = Field(default=0.0, ge=0.0, le=1.0)
    avg_days_late: float = Field(default=0.0, ge=0.0)
    prior_broken_promises_count: int = Field(default=0, ge=0)
    prior_disputes_count: int = Field(default=0, ge=0)


class InvoiceIn(BaseModel):
    """One invoice in an ingest batch."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    issue_date: date
    due_date: date
    payment_terms_days: int = Field(default=30, gt=0)
    prior_reminders_sent: int = Field(default=0, ge=0)
    last_contact_at: datetime | None = None


class BatchIngestRequest(BaseModel):
    """A batch of customers and their invoices."""

    model_config = ConfigDict(protected_namespaces=())

    customers: list[CustomerIn] = Field(min_length=1)
    invoices: list[InvoiceIn] = Field(min_length=1)


class BatchIngestResponse(BaseModel):
    """What ingest actually wrote.

    Reports created and skipped separately: re-posting a batch is a normal
    thing to do while demoing, and it must be idempotent rather than either
    failing or silently duplicating invoices.
    """

    model_config = ConfigDict(protected_namespaces=())

    customers_created: int = 0
    customers_skipped: int = 0
    invoices_created: int = 0
    invoices_skipped: int = 0
    unknown_customer_ids: list[str] = Field(default_factory=list)


class PromiseOut(BaseModel):
    """A promise as returned by the API."""

    model_config = ConfigDict(protected_namespaces=())

    promise_id: str
    promised_amount: float
    promised_date: date
    currency: str
    status: str
    created_at: datetime
    resolved_at: datetime | None = None
    broken_promise_score: float | None = None


class InvoiceOut(BaseModel):
    """One invoice's current state."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    customer_id: str
    customer_name: str
    amount: float
    amount_paid: float
    outstanding: float
    currency: str
    issue_date: date
    due_date: date
    days_overdue: int
    status: InvoiceStatus
    escalation_state: EscalationState
    ladder_index: int
    prior_reminders_sent: int
    last_contact_at: datetime | None = None
    payment_link_url: str | None = None
    paid_at: datetime | None = None
    promises: list[PromiseOut] = Field(default_factory=list)


class InvoiceListItem(BaseModel):
    """One row in the paginated work-queue list.

    Adds the tier/p_recovery/expected_value fields the queue page needs, by running
    the same scorer used by the batch report but dry-run and only on the
    page-sized slice -- no state is persisted and nothing is sent.
    """

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    customer_id: str
    customer_name: str
    amount: float
    amount_paid: float
    outstanding: float
    currency: str
    due_date: date
    days_overdue: int
    status: InvoiceStatus
    escalation_state: EscalationState
    ladder_index: int
    prior_reminders_sent: int
    last_contact_at: datetime | None = None
    tier: InterventionTier | None = None
    p_recovery: float | None = None
    expected_value: float | None = None
    promise_status: PromiseStatus | str | None = None
    promise_due_date: date | None = None
    rationale: str | None = None


class InvoiceListResponse(BaseModel):
    """Paginated list of invoices for the work queue page."""

    model_config = ConfigDict(protected_namespaces=())

    items: list[InvoiceListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class DecisionTraceOut(BaseModel):
    """One entry in an invoice's decision trail."""

    model_config = ConfigDict(protected_namespaces=())

    seq: int
    event: str
    outcome: str
    reason: str
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime
    prev_hash: str
    entry_hash: str


class AuditTrailOut(BaseModel):
    """An invoice's full decision trail, plus whether it still verifies."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    entries: list[DecisionTraceOut]
    entry_count: int
    #: Whether the whole ledger's hash chain re-derives. Reported alongside the
    #: entries so a caller reading the trail knows whether to trust it.
    chain_verified: bool


class PolicyDecisionOut(BaseModel):
    """The gate's verdict, as returned by run-cycle."""

    model_config = ConfigDict(protected_namespaces=())

    allowed: bool
    reason: str
    violations: list[dict[str, str]] = Field(default_factory=list)
    adjustments: list[dict[str, Any]] = Field(default_factory=list)
    effective_discount_pct: float = 0.0
    effective_discount_amount: float = 0.0


class ExecutionOut(BaseModel):
    """What the executor actually did, if it ran.

    Present only when the gate approved a contact. ``status`` distinguishes a
    delivered message from a simulated one from a failure, so a caller can
    never read "we contacted them" out of a run where nothing was sent.
    """

    model_config = ConfigDict(protected_namespaces=())

    status: str
    delivered: bool
    dry_run: bool
    subject: str = ""
    body_preview: str = ""
    provider_message_id: str | None = None
    payment_link_id: str | None = None
    payment_link_url: str | None = None
    payment_link_reused: bool = False
    amount_requested: float = 0.0
    error: str | None = None


class MLWorkflowStep(BaseModel):
    """Step-by-step trace of how an ML model or gate evaluated this case."""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    name: str
    model_type: str
    status: str
    verdict: str
    score: float | None = None
    score_label: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunCycleResponse(BaseModel):
    """The full, explainable result of one decision cycle."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    tier: InterventionTier
    p_recovery: float
    expected_value: float
    outstanding: float
    rationale: str
    top_drivers: list[dict[str, Any]] = Field(default_factory=list)

    action_type: str | None = None
    ladder_step: str = ""
    decision: PolicyDecisionOut | None = None

    transitioned: bool = False
    state_before: EscalationState
    state_after: EscalationState
    reason: str = ""
    terminal: bool = False
    #: Set when the scorer ran without a trained model. Surfaced, never hidden.
    scorer_fallback: bool = True
    scorer_version: str = ""

    #: Present only when the gate approved a contact and the executor ran.
    #: ``None`` means nothing was sent, and says so rather than leaving the
    #: caller to infer it from the tier.
    execution: ExecutionOut | None = None

    # Unified ML features validation and workflow trace
    timing_arm: str | None = None
    timing_expected_rate: float | None = None
    timing_scheduled_for: str | None = None
    timing_fallback: bool = False
    drift_flagged: bool | None = None
    drift_score: float | None = None
    drift_drivers: list[dict[str, Any]] = Field(default_factory=list)
    broken_promise_score: float | None = None
    broken_promise_status: str | None = None
    ml_workflow: list[MLWorkflowStep] = Field(default_factory=list)

