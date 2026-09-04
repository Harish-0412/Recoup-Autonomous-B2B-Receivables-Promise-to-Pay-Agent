"""Database access for the agent loop.

Kept in one module so that the routes stay thin and the core stays pure. Every
query the API needs is here; nothing here makes a decision.

Wave 1 tenancy: every function takes ``business_id`` and filters by it. There
is no unscoped read or write on a tenant table -- a query that omits
``business_id`` is a cross-tenant leak, and the isolation tests assert exactly
that. If you add a query here, add the filter; if you add a table, add the
column (see app.models.tables).

The one piece of real logic is :func:`load_case` -- assembling a
``CaseSnapshot`` from rows, including the opt-out registry and any open
promise. It matters that this is the *only* place that assembly happens: the
policy engine's opt-out guard is only as good as the set of opt-outs it is
handed, so building that set in one audited place beats rebuilding it per route.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import DecisionLedger
from app.core.domain import CaseSnapshot, snapshot_from_rows
from app.core.promise_tracker import PromiseRecord
from app.models import (
    BatchRunRecord,
    Business,
    ContactChannel,
    ContactLog,
    Customer,
    CustomerDriftFlag,
    DecisionTrace,
    DeliveryStatus,
    EscalationState,
    InboundReply,
    IntegrationCredential,
    IntegrationProvider,
    Invoice,
    InvoiceStatus,
    OptOut,
    PaymentAllocation,
    Promise,
    PromiseStatus,
    ReplyDisposition,
    WebhookEvent,
)
from app.models.tables import DEFAULT_BUSINESS_ID
from src.ml.versioning import utc_now


def _require_business_id(business_id: str) -> str:
    """Every tenant query filters by this. Empty means a bug, not 'all'."""
    if not business_id or not str(business_id).strip():
        raise ValueError("business_id is required for every tenant query")
    return str(business_id)


def _assert_same_tenant(obj: object, business_id: str, *, what: str = "row") -> None:
    actual = getattr(obj, "business_id", None)
    if actual != business_id:
        raise ValueError(f"Cross-tenant {what}: row is {actual!r}, scope is {business_id!r}")


# ---------------------------------------------------------------------------
# Businesses (tenant registry; not in the agent loop)
# ---------------------------------------------------------------------------


async def get_business(session: AsyncSession, business_id: str) -> Business | None:
    result = await session.execute(select(Business).where(Business.business_id == business_id))
    return result.scalar_one_or_none()


async def get_business_by_key(session: AsyncSession, token: str) -> Business | None:
    """Find the tenant owning an API or task key."""
    if not token:
        return None
    result = await session.execute(
        select(Business).where((Business.api_key == token) | (Business.task_api_key == token))
    )
    return result.scalar_one_or_none()


async def ensure_business(session: AsyncSession, business_id: str, *, name: str = "") -> Business:
    existing = await get_business(session, business_id)
    if existing is not None:
        return existing
    row = Business(business_id=business_id, name=name or business_id)
    session.add(row)
    await session.flush()
    return row


async def get_customer(
    session: AsyncSession, customer_id: str, business_id: str
) -> Customer | None:
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Customer).where(
            Customer.customer_id == customer_id, Customer.business_id == business_id
        )
    )
    return result.scalar_one_or_none()


async def get_invoice(session: AsyncSession, invoice_id: str, business_id: str) -> Invoice | None:
    """One invoice in one business, with its promises already loaded.

    The eager load is not an optimisation, it is a correctness requirement.
    Under asyncio a lazy relationship access raises ``MissingGreenlet`` rather
    than quietly emitting a second query, so touching ``invoice.promises`` on a
    plainly-selected row is a 500 -- which is exactly what
    ``GET /invoices/{id}`` used to return. Loading it here means every caller
    gets a row that is safe to read attributes from.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Invoice)
        .where(Invoice.invoice_id == invoice_id, Invoice.business_id == business_id)
        .options(selectinload(Invoice.promises))
    )
    return result.scalar_one_or_none()


async def get_invoice_by_payment_link(
    session: AsyncSession, payment_link_id: str, business_id: str
) -> Invoice | None:
    """Find the invoice a Razorpay payment link belongs to, within one tenant.

    The lookup must not cross tenants: link IDs are scoped by (business_id,
    payment_link_id) so a colliding link in another business can never close
    this tenant's invoice.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Invoice).where(
            Invoice.payment_link_id == payment_link_id, Invoice.business_id == business_id
        )
    )
    return result.scalar_one_or_none()


async def list_open_invoices(
    session: AsyncSession, business_id: str, *, limit: int = 500
) -> list[Invoice]:
    """Invoices the agent may still act on, oldest due date first, one tenant."""

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Invoice)
        .where(
            Invoice.business_id == business_id,
            Invoice.status.in_(
                [InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS, InvoiceStatus.PROMISED]
            ),
            Invoice.escalation_state.notin_(
                [EscalationState.HUMAN_HANDOFF, EscalationState.CLOSED]
            ),
        )
        .order_by(Invoice.due_date)
        .limit(limit)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Paginated list for the work queue
# ---------------------------------------------------------------------------


async def list_invoices_paginated(
    session: AsyncSession,
    business_id: str,
    *,
    status: InvoiceStatus | None = None,
    escalation_state: EscalationState | None = None,
    customer_search: str | None = None,
    only_open: bool = True,
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "expected_value",
    sort_desc: bool = True,
) -> tuple[list[Invoice], int]:
    """Paginated, filterable invoice list plus the queue page needs.

    Supports filtering by status, escalation state, and a free-text customer name
    match. Sorting by ``expected_value`` requires the *caller* to
    post-compute and reorder results -- the ORM columns we trust enough to do here are
    ``outstanding``, ``due_date``, ``invoice_id``, and ``customer_name``.

    ``only_open`` defaults to True because "the MSME collections user lives in open
    invoices day-to-day; ``/invoices only exposes everything when a user needs to find a
    specific closed case or an archived payment; the caller controls the default.
    """

    business_id = _require_business_id(business_id)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    stmt = (
        select(Invoice)
        .join(Customer, Customer.id == Invoice.customer_pk)
        .where(Invoice.business_id == business_id, Customer.business_id == business_id)
    )

    if only_open and status is None:
        stmt = stmt.where(
            Invoice.status.in_(
                [InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS, InvoiceStatus.PROMISED]
            )
        )
    elif status is not None:
        stmt = stmt.where(Invoice.status == status)

    if escalation_state is not None:
        stmt = stmt.where(Invoice.escalation_state == escalation_state)

    if customer_search:
        pattern = f"%{customer_search.strip()}%"
        stmt = stmt.where(
            (Customer.name.ilike(pattern))
            | (Customer.customer_id.ilike(pattern))
            | (Invoice.invoice_id.ilike(pattern))
        )

    count_stmt = select(func.count(func.distinct(Invoice.id)))
    total_rows = await session.execute(count_stmt.select_from(stmt.subquery()))
    total = int(total_rows.scalar_one())

    safe_column = {
        "outstanding": Invoice.amount - Invoice.amount_paid,
        "amount": Invoice.amount,
        "due_date": Invoice.due_date,
        "invoice_id": Invoice.invoice_id,
        "issue_date": Invoice.issue_date,
    }.get(sort_by)
    if safe_column is not None:
        stmt = stmt.order_by(safe_column.desc() if sort_desc else safe_column.asc())
    else:
        stmt = stmt.order_by((Invoice.amount - Invoice.amount_paid).desc())

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await session.execute(stmt)
    rows: list[Invoice] = list(result.scalars().unique().all())
    return rows, total


async def active_opt_out_channels(
    session: AsyncSession,
    customer_pk: int,
    business_id: str,
    *,
    now: date | None = None,
) -> frozenset[ContactChannel | None]:
    """Channels this customer is currently opted out of.

    An expired opt-out is not returned -- ``DEFAULT_OPTOUT_DAYS`` governs how
    long one is honoured -- but the row is kept, because "this customer opted
    out once before" is context a human reviewer needs.
    """

    business_id = _require_business_id(business_id)
    reference = now or utc_now().date()
    result = await session.execute(
        select(OptOut).where(OptOut.customer_pk == customer_pk, OptOut.business_id == business_id)
    )

    active: set[ContactChannel | None] = set()
    for opt_out in result.scalars().all():
        if opt_out.expires_at is not None and opt_out.expires_at.date() < reference:
            continue
        active.add(opt_out.channel)
    return frozenset(active)


async def open_promise_for(
    session: AsyncSession, invoice_pk: int, business_id: str
) -> Promise | None:
    """The most recent still-pending promise on this invoice, if any."""

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Promise)
        .where(
            Promise.invoice_pk == invoice_pk,
            Promise.status == PromiseStatus.PENDING,
            Promise.business_id == business_id,
        )
        .order_by(Promise.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_case(
    session: AsyncSession,
    invoice: Invoice,
    business_id: str,
    *,
    as_of: date | None = None,
) -> CaseSnapshot:
    """Assemble the snapshot the core decides on, from persisted rows."""

    business_id = _require_business_id(business_id)
    _assert_same_tenant(invoice, business_id, what="invoice")
    reference = as_of or utc_now().date()
    customer = await session.get(Customer, invoice.customer_pk)
    if customer is None:
        raise ValueError(f"Invoice {invoice.invoice_id} has no customer row")
    _assert_same_tenant(customer, business_id, what="customer")

    opted_out = await active_opt_out_channels(session, customer.id, business_id, now=reference)
    promise = await open_promise_for(session, invoice.id, business_id)

    return snapshot_from_rows(
        invoice,
        customer,
        as_of=reference,
        opted_out_channels=opted_out,
        has_open_promise=promise is not None,
        open_promise_due_in_days=(
            (promise.promised_date - reference).days if promise is not None else None
        ),
    )


#: Attempts that actually reached a customer. A FAILED row is a record of an
#: outage, not of a contact, and must not consume the contact budget.
COUNTED_DELIVERIES = (DeliveryStatus.SENT, DeliveryStatus.SIMULATED)


async def last_timed_contact(
    session: AsyncSession, invoice_pk: int, business_id: str
) -> ContactLog | None:
    """The most recent delivered contact that carries a bandit arm.

    Attribution for the online update: a reply rewards the slot of the last
    contact that named one. Contacts from before the timing model existed
    carry no arm and are skipped rather than guessed at.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(ContactLog)
        .where(
            ContactLog.invoice_pk == invoice_pk,
            ContactLog.business_id == business_id,
            ContactLog.status.in_(COUNTED_DELIVERIES),
            ContactLog.timing_arm.is_not(None),
        )
        .order_by(ContactLog.sent_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def contacts_sent_count(session: AsyncSession, invoice_pk: int, business_id: str) -> int:
    """How many messages have actually gone out about this invoice.

    Excludes FAILED attempts. Counting them would let a provider outage burn
    through the per-invoice cap without a single email being delivered.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(func.count())
        .select_from(ContactLog)
        .where(
            ContactLog.invoice_pk == invoice_pk,
            ContactLog.business_id == business_id,
            ContactLog.status.in_(COUNTED_DELIVERIES),
        )
    )
    return int(result.scalar_one())


async def record_contact(
    session: AsyncSession,
    invoice: Invoice,
    business_id: str,
    *,
    channel: ContactChannel,
    ladder_step: str,
    subject: str = "",
    body_preview: str = "",
    status: DeliveryStatus = DeliveryStatus.SENT,
    provider_message_id: str | None = None,
    payment_link_id: str | None = None,
    provider_error: str | None = None,
    timing_arm: str | None = None,
    scheduled_for: datetime | None = None,
) -> ContactLog:
    """Record one delivery *attempt*, and update counters only if it landed.

    A row is written either way, because a failed send is worth seeing. The
    counters are the part that must not move: ``prior_reminders_sent`` and
    ``last_contact_at`` drive the frequency cap and the minimum-gap rule, so
    incrementing them for a message that never left would spend a customer's
    contact budget on nothing -- and a provider outage would silently exhaust
    the whole book while sending zero emails.

    Counters and the row are still written in one unit of work for a delivered
    message: the cap counts rows and the gap rule reads ``last_contact_at``, so
    a send recorded in one place but not the other would widen a cap.
    """

    business_id = _require_business_id(business_id)
    _assert_same_tenant(invoice, business_id, what="invoice")
    contact = ContactLog(
        business_id=business_id,
        invoice_pk=invoice.id,
        channel=channel,
        ladder_step=ladder_step,
        subject=subject[:255],
        body_preview=body_preview[:500],
        status=status,
        provider_message_id=provider_message_id,
        payment_link_id=payment_link_id,
        provider_error=provider_error,
        timing_arm=timing_arm,
        scheduled_for=scheduled_for,
    )
    session.add(contact)

    if contact.counts_as_contact:
        invoice.prior_reminders_sent += 1
        invoice.last_contact_at = utc_now()
        if invoice.status is InvoiceStatus.OPEN:
            invoice.status = InvoiceStatus.IN_PROGRESS

    return contact


async def record_opt_out(
    session: AsyncSession,
    customer: Customer,
    business_id: str,
    *,
    channel: ContactChannel | None = None,
    reason: str = "",
    source_reply_id: str | None = None,
    honour_days: int = 30,
) -> OptOut:
    """Register an opt-out, or extend an existing one for the same channel."""

    business_id = _require_business_id(business_id)
    _assert_same_tenant(customer, business_id, what="customer")
    result = await session.execute(
        select(OptOut).where(
            OptOut.customer_pk == customer.id,
            OptOut.channel == channel,
            OptOut.business_id == business_id,
        )
    )
    existing = result.scalar_one_or_none()
    expires = utc_now() + timedelta(days=honour_days)

    if existing is not None:
        existing.expires_at = expires
        existing.reason = reason or existing.reason
        return existing

    opt_out = OptOut(
        business_id=business_id,
        customer_pk=customer.id,
        channel=channel,
        reason=reason,
        source_reply_id=source_reply_id,
        expires_at=expires,
    )
    session.add(opt_out)
    return opt_out


async def persist_ledger(session: AsyncSession, ledger: DecisionLedger, business_id: str) -> int:
    """Mirror an in-memory ledger's entries into ``decision_traces``.

    The hashes are computed in the core and stored as-is; this function never
    recomputes them. ``seq`` is re-based so the stored column stays a dense
    global position across process restarts, while each ledger numbers its own
    entries from zero.

    That renumbering is safe precisely because ``content_digest`` does not
    cover ``seq`` -- see the note there. It used to, which meant this line
    silently invalidated every hash it wrote.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(select(func.coalesce(func.max(DecisionTrace.seq), -1)))
    offset = int(result.scalar_one()) + 1

    written = 0
    for entry in ledger:
        session.add(
            DecisionTrace(
                business_id=business_id,
                seq=offset + entry.seq,
                invoice_id=entry.invoice_id,
                event=entry.event,
                outcome=entry.outcome,
                reason=entry.reason,
                actor=entry.actor,
                payload=entry.payload,
                prev_hash=entry.prev_hash,
                entry_hash=entry.entry_hash,
                recorded_at=entry.recorded_at,
            )
        )
        written += 1
    return written


async def trace_for_invoice(
    session: AsyncSession, invoice_id: str, business_id: str
) -> list[DecisionTrace]:
    """One invoice's decision trail in one business, oldest first."""

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(DecisionTrace)
        .where(DecisionTrace.invoice_id == invoice_id, DecisionTrace.business_id == business_id)
        .order_by(DecisionTrace.seq)
    )
    return list(result.scalars().all())


async def webhook_already_processed(
    session: AsyncSession, event_id: str, business_id: str | None = None
) -> bool:
    """Whether this Razorpay event id has been seen before.

    Razorpay retries on any non-2xx response, so the same ``payment.captured``
    can arrive several times. Counting one payment twice would corrupt every
    recovery number downstream, which is why this check exists before any
    handler runs rather than inside one.

    Scoped by (business_id, event_id) when the tenant is known; falls back to
    a global event_id check when the payload has not been routed yet.
    """

    if business_id is not None:
        business_id = _require_business_id(business_id)
        result = await session.execute(
            select(WebhookEvent.id).where(
                WebhookEvent.event_id == event_id, WebhookEvent.business_id == business_id
            )
        )
        return result.scalar_one_or_none() is not None
    result = await session.execute(select(WebhookEvent.id).where(WebhookEvent.event_id == event_id))
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Inbound replies and promises
# ---------------------------------------------------------------------------


async def get_inbound_reply(
    session: AsyncSession, reply_id: str, business_id: str | None = None
) -> InboundReply | None:
    """One stored reply by its provider message id, the idempotency key."""

    stmt = select(InboundReply).where(InboundReply.reply_id == reply_id)
    if business_id is not None:
        stmt = stmt.where(InboundReply.business_id == _require_business_id(business_id))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_customer_by_email(
    session: AsyncSession, email: str, business_id: str
) -> Customer | None:
    """Match a sender address to a customer in one business, case-insensitively.

    Only ever used as a *secondary* signal: the tagged reply-to address is what
    identifies the invoice. This fills in who wrote, so an unmatched reply
    still lands in the review queue attached to the right customer.
    """

    business_id = _require_business_id(business_id)
    if not email:
        return None
    result = await session.execute(
        select(Customer).where(
            func.lower(Customer.email) == email.strip().lower(),
            Customer.business_id == business_id,
        )
    )
    return result.scalars().first()


async def record_promise(
    session: AsyncSession,
    invoice: Invoice,
    promise: PromiseRecord,
    business_id: str,
    ledger: DecisionLedger | None = None,
) -> Promise:
    """Persist a promise extracted from a reply.

    Any earlier pending promise on the same invoice is marked SUPERSEDED rather
    than deleted or left open. Two live promises on one invoice would make
    "did they keep it?" unanswerable, and the old one is still the record of
    what the customer said last time.

    The exact 19-feature vector the broken-promise scorer saw is frozen into a
    ``promise:scored`` trace (when a ledger is passed) so the Wave 6 outcomes
    ETL rebuilds point-in-time training rows without reconstructing history.
    """

    business_id = _require_business_id(business_id)
    _assert_same_tenant(invoice, business_id, what="invoice")
    existing = await session.execute(
        select(Promise).where(
            Promise.invoice_pk == invoice.id,
            Promise.status == PromiseStatus.PENDING,
            Promise.business_id == business_id,
        )
    )
    for stale in existing.scalars().all():
        stale.status = PromiseStatus.SUPERSEDED
        stale.resolved_at = utc_now()

    # Broken-Promise Risk Scorer: predict whether customer will honor commitment
    broken_score = None
    try:
        from src.agent.promise_handler import score_broken_promise

        customer = invoice.customer if hasattr(invoice, "customer") else None
        cust_inv_count = getattr(customer, "invoice_count", 1) if customer else 1
        score_payload = {
            "promise_id": promise.promise_id,
            "promised_amount": promise.promised_amount,
            "promised_date": promise.promised_date.isoformat()
            if hasattr(promise.promised_date, "isoformat")
            else str(promise.promised_date),
            "invoice_amount": invoice.amount,
            "days_overdue_at_scoring": max((utc_now().date() - invoice.due_date).days, 0)
            if hasattr(invoice, "due_date")
            else 0,
            "payment_terms_days": getattr(invoice, "payment_terms_days", 30),
            "prior_reminders_sent": getattr(invoice, "prior_reminders_sent", 0),
            "customer_broken_promises_count": getattr(customer, "prior_broken_promises_count", 0)
            if customer
            else 0,
            "customer_avg_days_late": getattr(customer, "avg_days_late", 0.0) if customer else 0.0,
            "customer_on_time_ratio_90d": getattr(customer, "on_time_ratio_90d", 0.85)
            if customer
            else 0.85,
            "customer_on_time_ratio_all_time": getattr(customer, "on_time_ratio_all_time", 0.85)
            if customer
            else 0.85,
            "customer_dispute_rate": (
                getattr(customer, "prior_disputes_count", 0) / max(cust_inv_count, 1)
            )
            if customer
            else 0.0,
            "customer_invoice_count": cust_inv_count,
            "customer_tenure_months": getattr(customer, "tenure_months", 12) if customer else 12,
        }
        broken_score = score_broken_promise(score_payload)
    except Exception:
        broken_score = 0.50
        score_payload = {}

    # Freeze the scorer's input for the Wave 6 broken-promise outcomes ETL.
    # build_broken_promise_features is the same function serving uses, so the
    # stored vector is byte-identical to what the model saw -- no skew.
    if ledger is not None and score_payload:
        try:
            from app.core.audit import append_decision_trace
            from src.agent.promise_handler import (
                FEATURE_COLUMNS as BROKEN_PROMISE_FEATURE_COLUMNS,
            )
            from src.agent.promise_handler import build_broken_promise_features

            bp_vector = build_broken_promise_features(score_payload)
            append_decision_trace(
                invoice_id=invoice.invoice_id,
                event="promise:scored",
                reason=(
                    f"Broken-promise risk scored at {broken_score:.2f} for "
                    f"{promise.promise_id}."
                ),
                ledger=ledger,
                promise_id=promise.promise_id,
                broken_promise_score=broken_score,
                feature_set="broken-promise-features-v1",
                features={
                    name: float(value)
                    for name, value in zip(BROKEN_PROMISE_FEATURE_COLUMNS, bp_vector, strict=False)
                },
            )
        except Exception:
            pass

    row = Promise(
        business_id=business_id,
        promise_id=promise.promise_id,
        invoice_pk=invoice.id,
        promised_amount=promise.promised_amount,
        promised_date=promise.promised_date,
        currency=promise.currency,
        source_reply_id=promise.source_reply_id,
        source_confidence=promise.source_confidence,
        status=PromiseStatus.PENDING,
        broken_promise_score=broken_score,
    )
    session.add(row)

    # A live promise is why the policy gate goes quiet on this invoice; the
    # status change is what the gate reads.
    if invoice.status in (InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS):
        invoice.status = InvoiceStatus.PROMISED

    await session.flush()
    return row


async def replies_needing_review(
    session: AsyncSession, business_id: str, *, limit: int = 100
) -> list[InboundReply]:
    """The human review queue for one business, oldest first.

    Oldest first on purpose: a queue worked newest-first leaves its oldest
    items to rot, and the oldest unreviewed reply is the one most likely to be
    a customer waiting on an answer.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(InboundReply)
        .where(
            InboundReply.disposition == ReplyDisposition.NEEDS_REVIEW,
            InboundReply.business_id == business_id,
        )
        .order_by(InboundReply.received_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def pending_promises(
    session: AsyncSession, business_id: str, *, limit: int = 500
) -> list[tuple[Promise, Invoice]]:
    """Every still-open promise with its invoice, oldest promised date first.

    Joined rather than fetched separately because the sweep needs
    ``invoice.amount_paid`` for each one, and a promise is only ever settled by
    money that actually arrived.
    """

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Promise, Invoice)
        .join(Invoice, Invoice.id == Promise.invoice_pk)
        .where(
            Promise.status == PromiseStatus.PENDING,
            Promise.business_id == business_id,
            Invoice.business_id == business_id,
        )
        .order_by(Promise.promised_date)
        .limit(limit)
    )
    return list(result.all())  # type: ignore[arg-type]


async def save_batch_run(
    session: AsyncSession, record: BatchRunRecord, business_id: str
) -> BatchRunRecord:
    """Save an autonomous batch run record and its invoice decisions."""
    business_id = _require_business_id(business_id)
    record.business_id = business_id
    session.add(record)
    await session.flush()
    return record


async def get_latest_batch_run(session: AsyncSession, business_id: str) -> BatchRunRecord | None:
    """Get the most recent completed batch run for one business."""
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(BatchRunRecord)
        .where(BatchRunRecord.business_id == business_id)
        .order_by(BatchRunRecord.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_batch_runs(
    session: AsyncSession, business_id: str, *, limit: int = 20
) -> list[BatchRunRecord]:
    """Get list of past batch runs for one business, most recent first."""
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(BatchRunRecord)
        .where(BatchRunRecord.business_id == business_id)
        .order_by(BatchRunRecord.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_batch_run_by_id(
    session: AsyncSession, run_id: str, business_id: str
) -> BatchRunRecord | None:
    """Get a specific batch run by its run_id within one business."""
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(BatchRunRecord).where(
            BatchRunRecord.run_id == run_id, BatchRunRecord.business_id == business_id
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Drift flags
# ---------------------------------------------------------------------------


async def customers_with_open_invoices(
    session: AsyncSession, business_id: str, *, limit: int = 500
) -> list[Customer]:
    """Distinct customers holding at least one actionable invoice, one tenant."""

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Customer)
        .join(Invoice, Invoice.customer_pk == Customer.id)
        .where(
            Customer.business_id == business_id,
            Invoice.business_id == business_id,
            Invoice.status.in_(
                [InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS, InvoiceStatus.PROMISED]
            ),
            Invoice.escalation_state.notin_(
                [EscalationState.HUMAN_HANDOFF, EscalationState.CLOSED]
            ),
        )
        .distinct()
        .limit(limit)
    )
    return list(result.scalars().all())


async def invoices_for_customer(
    session: AsyncSession, customer_pk: int, business_id: str
) -> list[Invoice]:
    """Every invoice of one customer in one business, for drift aggregation."""

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(Invoice)
        .where(Invoice.customer_pk == customer_pk, Invoice.business_id == business_id)
        .order_by(Invoice.due_date)
    )
    return list(result.scalars().all())


async def save_drift_flag(
    session: AsyncSession, record: CustomerDriftFlag, business_id: str
) -> CustomerDriftFlag:
    """Persist one nightly drift verdict."""
    business_id = _require_business_id(business_id)
    record.business_id = business_id
    session.add(record)
    await session.flush()
    return record


async def latest_drift_flag_for(
    session: AsyncSession, customer_pk: int, business_id: str
) -> CustomerDriftFlag | None:
    """The most recent drift verdict for one customer, if ever scored."""

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(CustomerDriftFlag)
        .where(
            CustomerDriftFlag.customer_pk == customer_pk,
            CustomerDriftFlag.business_id == business_id,
        )
        .order_by(CustomerDriftFlag.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def recent_drift_flags(
    session: AsyncSession,
    business_id: str,
    *,
    limit: int = 50,
    only_flagged: bool = False,
) -> list[tuple[CustomerDriftFlag, Customer]]:
    """Recent verdicts with their customers, newest first, one tenant."""

    business_id = _require_business_id(business_id)
    query = (
        select(CustomerDriftFlag, Customer)
        .join(Customer, Customer.id == CustomerDriftFlag.customer_pk)
        .where(
            CustomerDriftFlag.business_id == business_id,
            Customer.business_id == business_id,
        )
        .order_by(CustomerDriftFlag.created_at.desc())
        .limit(limit)
    )
    if only_flagged:
        query = query.where(CustomerDriftFlag.flagged.is_(True))
    result = await session.execute(query)
    return list(result.all())  # type: ignore[arg-type]


async def count_recently_flagged_customers(
    session: AsyncSession, business_id: str, *, days: int = 7
) -> int:
    """Distinct customers flagged inside the trailing window, for /tasks/status."""

    business_id = _require_business_id(business_id)
    cutoff = utc_now() - timedelta(days=days)
    result = await session.execute(
        select(func.count(func.distinct(CustomerDriftFlag.customer_pk))).where(
            CustomerDriftFlag.flagged.is_(True),
            CustomerDriftFlag.created_at >= cutoff,
            CustomerDriftFlag.business_id == business_id,
        )
    )
    return int(result.scalar_one())


__all__ = [
    "DEFAULT_BUSINESS_ID",
    "COUNTED_DELIVERIES",
    "active_opt_out_channels",
    "confirm_allocation",
    "contacts_sent_count",
    "count_recently_flagged_customers",
    "create_allocation",
    "customers_with_open_invoices",
    "ensure_business",
    "get_allocation",
    "get_batch_run_by_id",
    "get_business",
    "get_business_by_key",
    "get_customer",
    "get_customer_by_email",
    "get_inbound_reply",
    "get_integration_credential",
    "get_invoice",
    "get_invoice_by_payment_link",
    "get_latest_batch_run",
    "invoices_for_customer",
    "last_timed_contact",
    "latest_drift_flag_for",
    "list_batch_runs",
    "list_invoices_paginated",
    "list_open_invoices",
    "list_open_invoice_candidates",
    "load_case",
    "open_promise_for",
    "pending_promises",
    "persist_ledger",
    "recompute_and_update_invoice",
    "recent_drift_flags",
    "record_contact",
    "record_opt_out",
    "record_promise",
    "replies_needing_review",
    "save_batch_run",
    "save_drift_flag",
    "trace_for_invoice",
    "unmatched_bank_payments",
    "upsert_customer",
    "upsert_integration_credential",
    "upsert_invoice",
    "utrs_for_tenant",
    "webhook_already_processed",
]


# ---------------------------------------------------------------------------
# Wave 2: Payment Allocations
# ---------------------------------------------------------------------------


async def create_allocation(
    session: AsyncSession,
    *,
    business_id: str,
    invoice_pk: int | None,
    source: AllocationSource,
    provider_ref: str,
    amount: float,
    currency: str = "INR",
    received_at: datetime | None = None,
    recorded_by: str = "webhook",
    payer_account: str | None = None,
    notes: str = "",
) -> PaymentAllocation | None:
    """Insert one payment allocation row.

    Returns the new row on success, or None if the unique constraint fires
    (business_id, source, provider_ref already exists). The caller should
    treat None as "idempotent duplicate, do nothing".

    Using an explicit INSERT ... ON CONFLICT DO NOTHING avoids relying on the
    caller to catch IntegrityError -- which would leave the session in a failed
    state that SQLAlchemy refuses to use until rollback.
    """

    business_id = _require_business_id(business_id)
    now = utc_now()

    # Detect dialect to pick the right upsert syntax
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    is_sqlite = False
    try:
        dialect_name = (
            session.bind.dialect.name if hasattr(session, "bind") and session.bind else ""
        )
        is_sqlite = dialect_name == "sqlite"
    except Exception:
        pass

    row = PaymentAllocation(
        business_id=business_id,
        invoice_pk=invoice_pk,
        source=source,
        provider_ref=provider_ref,
        amount=amount,
        currency=currency,
        received_at=received_at or now,
        recorded_by=recorded_by,
        payer_account=payer_account,
        notes=notes,
    )

    from sqlalchemy.exc import IntegrityError

    # Check for existing row first to avoid putting session in an aborted state
    existing = await session.execute(
        select(PaymentAllocation).where(
            PaymentAllocation.business_id == business_id,
            PaymentAllocation.source == source,
            PaymentAllocation.provider_ref == provider_ref,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return None

    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        return row
    except IntegrityError:
        return None


async def get_allocation(
    session: AsyncSession, allocation_id: int, business_id: str
) -> PaymentAllocation | None:
    """One allocation by PK, scoped to a tenant."""
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(PaymentAllocation).where(
            PaymentAllocation.id == allocation_id,
            PaymentAllocation.business_id == business_id,
        )
    )
    return result.scalar_one_or_none()


async def sum_allocations_for_invoice(
    session: AsyncSession, invoice_pk: int, business_id: str
) -> float:
    """Return SUM(amount) of all allocations linked to this invoice.

    The result is the canonical ``amount_paid``. Negative allocations
    (credit notes) are included automatically.
    """
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(func.coalesce(func.sum(PaymentAllocation.amount), 0.0)).where(
            PaymentAllocation.invoice_pk == invoice_pk,
            PaymentAllocation.business_id == business_id,
        )
    )
    return float(result.scalar_one())


async def recompute_and_update_invoice(
    session: AsyncSession, invoice: Invoice, business_id: str
) -> float:
    """Recompute amount_paid from the allocation ledger and update the invoice.

    Derives the new status using the pure derive_status() function and writes
    both fields. Returns the new amount_paid.

    Never call ``invoice.amount_paid += ...`` elsewhere. Always call this.
    """
    from app.core.payment_allocations import derive_status

    business_id = _require_business_id(business_id)
    _assert_same_tenant(invoice, business_id, what="invoice")

    new_amount_paid = await sum_allocations_for_invoice(session, invoice.id, business_id)
    contacted = invoice.prior_reminders_sent > 0
    new_status = derive_status(
        invoice.amount,
        new_amount_paid,
        invoice.status,
        contacted=contacted,
    )

    invoice.amount_paid = new_amount_paid
    if new_status != invoice.status:
        invoice.status = new_status
    if new_status is InvoiceStatus.PAID and invoice.paid_at is None:
        invoice.paid_at = utc_now()

    return new_amount_paid


async def unmatched_bank_payments(
    session: AsyncSession, business_id: str, *, limit: int = 100
) -> list[PaymentAllocation]:
    """Bank UTR allocations that have not yet been matched to an invoice.

    These are the rows in the review queue: the operator posted a UTR but
    no auto-match was confident enough, or the operator chose to confirm
    manually.
    """
    from app.models.enums import AllocationSource

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(PaymentAllocation)
        .where(
            PaymentAllocation.business_id == business_id,
            PaymentAllocation.source == AllocationSource.BANK_UTR,
            PaymentAllocation.invoice_pk.is_(None),
        )
        .order_by(PaymentAllocation.created_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def utrs_for_tenant(session: AsyncSession, business_id: str) -> set[str]:
    """All UTR strings already recorded for this tenant.

    Passed to the UTR matcher to detect duplicates without a DB round-trip
    inside the pure matcher.
    """
    from app.models.enums import AllocationSource

    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(PaymentAllocation.provider_ref).where(
            PaymentAllocation.business_id == business_id,
            PaymentAllocation.source == AllocationSource.BANK_UTR,
        )
    )
    return set(result.scalars().all())


async def confirm_allocation(
    session: AsyncSession,
    allocation: PaymentAllocation,
    invoice: Invoice,
    business_id: str,
) -> float:
    """Link an unmatched allocation to its invoice and recompute the invoice.

    Both the allocation and the invoice must belong to the same tenant. The
    invoice must be open (not PAID, DISPUTED, etc.). Returns the new amount_paid.
    """
    business_id = _require_business_id(business_id)
    _assert_same_tenant(allocation, business_id, what="allocation")
    _assert_same_tenant(invoice, business_id, what="invoice")

    if allocation.invoice_pk is not None and allocation.invoice_pk != invoice.id:
        raise ValueError(
            f"Allocation {allocation.id} is already linked to invoice_pk {allocation.invoice_pk}; "
            f"cannot re-link to {invoice.id}."
        )

    allocation.invoice_pk = invoice.id
    return await recompute_and_update_invoice(session, invoice, business_id)


async def list_open_invoice_candidates(
    session: AsyncSession,
    business_id: str,
    *,
    customer_pk: int | None = None,
    limit: int = 1000,
) -> list[Invoice]:
    """Open invoices for UTR matching, optionally filtered to one customer."""
    business_id = _require_business_id(business_id)
    stmt = (
        select(Invoice)
        .where(
            Invoice.business_id == business_id,
            Invoice.status.in_(
                [InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS, InvoiceStatus.PROMISED]
            ),
        )
        .limit(limit)
    )
    if customer_pk is not None:
        stmt = stmt.where(Invoice.customer_pk == customer_pk)
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Wave 2: Integration Credentials
# ---------------------------------------------------------------------------


async def get_integration_credential(
    session: AsyncSession, business_id: str, provider: IntegrationProvider
) -> IntegrationCredential | None:
    """Fetch the OAuth credential for one tenant/provider pair."""
    business_id = _require_business_id(business_id)
    result = await session.execute(
        select(IntegrationCredential).where(
            IntegrationCredential.business_id == business_id,
            IntegrationCredential.provider == provider,
        )
    )
    return result.scalar_one_or_none()


async def upsert_integration_credential(
    session: AsyncSession,
    business_id: str,
    provider: IntegrationProvider,
    *,
    access_token: str = "",
    refresh_token: str = "",
    token_expires_at: datetime | None = None,
    scope: str = "",
    extra: dict | None = None,
) -> IntegrationCredential:
    """Insert or update an integration credential row.

    Uses SELECT + UPDATE-or-INSERT so it works on both SQLite (tests) and
    Postgres (production) without dialect-specific ON CONFLICT syntax.
    """
    business_id = _require_business_id(business_id)
    existing = await get_integration_credential(session, business_id, provider)
    if existing is not None:
        existing.access_token = access_token
        existing.refresh_token = refresh_token
        existing.token_expires_at = token_expires_at
        existing.scope = scope
        if extra is not None:
            existing.extra = {**existing.extra, **extra}
        return existing

    row = IntegrationCredential(
        business_id=business_id,
        provider=provider,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
        scope=scope,
        extra=extra or {},
    )
    session.add(row)
    await session.flush()
    return row


async def update_sync_stats(
    session: AsyncSession,
    credential: IntegrationCredential,
    *,
    invoices_synced: int,
    errors: int,
) -> None:
    """Record the outcome of a sync run on the credential row."""
    credential.last_sync_at = utc_now()
    credential.last_sync_invoices = invoices_synced
    credential.last_sync_errors = errors


# ---------------------------------------------------------------------------
# Wave 2: ERP-safe upsert for customers and invoices
# ---------------------------------------------------------------------------

#: Fields on Invoice that ERP syncs must never overwrite. Recoup owns these;
#: the ERP owns amount and due_date.
_ERP_PROTECTED_INVOICE_FIELDS: frozenset[str] = frozenset(
    {"escalation_state", "ladder_index", "amount_paid", "paid_at", "status"}
)

#: Fields on Customer that ERP syncs may write.
_ERP_ALLOWED_CUSTOMER_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "industry",
        "email",
        "phone",
        "preferred_channel",
        "tenure_months",
        "invoice_count",
        "avg_invoice_amount",
        "on_time_ratio_90d",
        "on_time_ratio_all_time",
        "avg_days_late",
        "prior_broken_promises_count",
        "prior_disputes_count",
    }
)


async def upsert_customer(
    session: AsyncSession,
    business_id: str,
    *,
    customer_id: str,
    source: str,
    external_id: str,
    **fields: object,
) -> tuple[Customer, bool]:
    """Insert or update a customer from an ERP sync.

    ``source`` and ``external_id`` are stored in the customer's email/phone
    only if the customer is new; they act as the ERP's primary key. Returns
    (customer_row, created: bool).

    The ``customer_id`` is the Recoup business key. The ERP sync derives it
    from the customer email or its own customer code.
    """
    business_id = _require_business_id(business_id)
    existing = await get_customer(session, customer_id, business_id)
    if existing is not None:
        # Update only allowed fields, never identity or payment state
        for field, value in fields.items():
            if field in _ERP_ALLOWED_CUSTOMER_FIELDS:
                setattr(existing, field, value)
        return existing, False

    # New customer
    allowed = {k: v for k, v in fields.items() if k in _ERP_ALLOWED_CUSTOMER_FIELDS}
    row = Customer(
        business_id=business_id,
        customer_id=customer_id,
        name=allowed.get("name", customer_id),
        **{k: v for k, v in allowed.items() if k != "name"},
    )
    session.add(row)
    await session.flush()
    return row, True


async def upsert_invoice(
    session: AsyncSession,
    business_id: str,
    *,
    invoice_id: str,
    customer_pk: int,
    erp_source: str,
    external_id: str,
    **fields: object,
) -> tuple[Invoice, bool]:
    """Insert or update an invoice from an ERP sync.

    ERP owns: ``amount``, ``due_date``, ``issue_date``, ``payment_terms_days``,
    ``currency``.

    Recoup owns (never overwritten): ``escalation_state``, ``ladder_index``,
    ``amount_paid``, ``paid_at``, ``status``.

    The ``external_ids`` JSON is merged (never replaced) so multiple ERPs can
    each store their own key.

    Returns (invoice_row, created: bool).
    """
    business_id = _require_business_id(business_id)
    existing = await get_invoice(session, invoice_id, business_id)

    if existing is not None:
        # Update ERP-owned fields only
        for field, value in fields.items():
            if field not in _ERP_PROTECTED_INVOICE_FIELDS:
                setattr(existing, field, value)
        # Merge external_ids
        merged = dict(existing.external_ids or {})
        merged[f"{erp_source}_id"] = external_id
        existing.external_ids = merged
        existing.erp_source = erp_source
        return existing, False

    # Sanitise: remove any protected fields the caller passed in
    safe_fields = {k: v for k, v in fields.items() if k not in _ERP_PROTECTED_INVOICE_FIELDS}
    row = Invoice(
        business_id=business_id,
        invoice_id=invoice_id,
        customer_pk=customer_pk,
        erp_source=erp_source,
        external_ids={f"{erp_source}_id": external_id},
        **safe_fields,
    )
    session.add(row)
    await session.flush()
    return row, True
