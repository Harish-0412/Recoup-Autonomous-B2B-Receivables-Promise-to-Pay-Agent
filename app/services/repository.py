"""Database access for the agent loop.

Kept in one module so that the routes stay thin and the core stays pure. Every
query the API needs is here; nothing here makes a decision.

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
    ContactChannel,
    ContactLog,
    Customer,
    DecisionTrace,
    DeliveryStatus,
    EscalationState,
    InboundReply,
    Invoice,
    InvoiceStatus,
    OptOut,
    Promise,
    PromiseStatus,
    ReplyDisposition,
    WebhookEvent,
)
from src.ml.versioning import utc_now


async def get_customer(session: AsyncSession, customer_id: str) -> Customer | None:
    result = await session.execute(select(Customer).where(Customer.customer_id == customer_id))
    return result.scalar_one_or_none()


async def get_invoice(session: AsyncSession, invoice_id: str) -> Invoice | None:
    """One invoice, with its promises already loaded.

    The eager load is not an optimisation, it is a correctness requirement.
    Under asyncio a lazy relationship access raises ``MissingGreenlet`` rather
    than quietly emitting a second query, so touching ``invoice.promises`` on a
    plainly-selected row is a 500 -- which is exactly what
    ``GET /invoices/{id}`` used to return. Loading it here means every caller
    gets a row that is safe to read attributes from.
    """

    result = await session.execute(
        select(Invoice)
        .where(Invoice.invoice_id == invoice_id)
        .options(selectinload(Invoice.promises))
    )
    return result.scalar_one_or_none()


async def get_invoice_by_payment_link(
    session: AsyncSession, payment_link_id: str
) -> Invoice | None:
    """Find the invoice a Razorpay payment link belongs to."""

    result = await session.execute(
        select(Invoice).where(Invoice.payment_link_id == payment_link_id)
    )
    return result.scalar_one_or_none()


async def list_open_invoices(session: AsyncSession, *, limit: int = 500) -> list[Invoice]:
    """Invoices the agent may still act on, oldest due date first."""

    result = await session.execute(
        select(Invoice)
        .where(
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

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    stmt = select(Invoice).join(Customer, Customer.id == Invoice.customer_pk)

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
    session: AsyncSession, customer_pk: int, *, now: date | None = None
) -> frozenset[ContactChannel | None]:
    """Channels this customer is currently opted out of.

    An expired opt-out is not returned -- ``DEFAULT_OPTOUT_DAYS`` governs how
    long one is honoured -- but the row is kept, because "this customer opted
    out once before" is context a human reviewer needs.
    """

    reference = now or utc_now().date()
    result = await session.execute(select(OptOut).where(OptOut.customer_pk == customer_pk))

    active: set[ContactChannel | None] = set()
    for opt_out in result.scalars().all():
        if opt_out.expires_at is not None and opt_out.expires_at.date() < reference:
            continue
        active.add(opt_out.channel)
    return frozenset(active)


async def open_promise_for(session: AsyncSession, invoice_pk: int) -> Promise | None:
    """The most recent still-pending promise on this invoice, if any."""

    result = await session.execute(
        select(Promise)
        .where(Promise.invoice_pk == invoice_pk, Promise.status == PromiseStatus.PENDING)
        .order_by(Promise.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def load_case(
    session: AsyncSession,
    invoice: Invoice,
    *,
    as_of: date | None = None,
) -> CaseSnapshot:
    """Assemble the snapshot the core decides on, from persisted rows."""

    reference = as_of or utc_now().date()
    customer = await session.get(Customer, invoice.customer_pk)
    if customer is None:
        raise ValueError(f"Invoice {invoice.invoice_id} has no customer row")

    opted_out = await active_opt_out_channels(session, customer.id, now=reference)
    promise = await open_promise_for(session, invoice.id)

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


async def last_timed_contact(session: AsyncSession, invoice_pk: int) -> ContactLog | None:
    """The most recent delivered contact that carries a bandit arm.

    Attribution for the online update: a reply rewards the slot of the last
    contact that named one. Contacts from before the timing model existed
    carry no arm and are skipped rather than guessed at.
    """

    result = await session.execute(
        select(ContactLog)
        .where(
            ContactLog.invoice_pk == invoice_pk,
            ContactLog.status.in_(COUNTED_DELIVERIES),
            ContactLog.timing_arm.is_not(None),
        )
        .order_by(ContactLog.sent_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def contacts_sent_count(session: AsyncSession, invoice_pk: int) -> int:
    """How many messages have actually gone out about this invoice.

    Excludes FAILED attempts. Counting them would let a provider outage burn
    through the per-invoice cap without a single email being delivered.
    """

    result = await session.execute(
        select(func.count())
        .select_from(ContactLog)
        .where(
            ContactLog.invoice_pk == invoice_pk,
            ContactLog.status.in_(COUNTED_DELIVERIES),
        )
    )
    return int(result.scalar_one())


async def record_contact(
    session: AsyncSession,
    invoice: Invoice,
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

    contact = ContactLog(
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
    *,
    channel: ContactChannel | None = None,
    reason: str = "",
    source_reply_id: str | None = None,
    honour_days: int = 30,
) -> OptOut:
    """Register an opt-out, or extend an existing one for the same channel."""

    result = await session.execute(
        select(OptOut).where(OptOut.customer_pk == customer.id, OptOut.channel == channel)
    )
    existing = result.scalar_one_or_none()
    expires = utc_now() + timedelta(days=honour_days)

    if existing is not None:
        existing.expires_at = expires
        existing.reason = reason or existing.reason
        return existing

    opt_out = OptOut(
        customer_pk=customer.id,
        channel=channel,
        reason=reason,
        source_reply_id=source_reply_id,
        expires_at=expires,
    )
    session.add(opt_out)
    return opt_out


async def persist_ledger(session: AsyncSession, ledger: DecisionLedger) -> int:
    """Mirror an in-memory ledger's entries into ``decision_traces``.

    The hashes are computed in the core and stored as-is; this function never
    recomputes them. ``seq`` is re-based so the stored column stays a dense
    global position across process restarts, while each ledger numbers its own
    entries from zero.

    That renumbering is safe precisely because ``content_digest`` does not
    cover ``seq`` -- see the note there. It used to, which meant this line
    silently invalidated every hash it wrote.
    """

    result = await session.execute(select(func.coalesce(func.max(DecisionTrace.seq), -1)))
    offset = int(result.scalar_one()) + 1

    written = 0
    for entry in ledger:
        session.add(
            DecisionTrace(
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


async def trace_for_invoice(session: AsyncSession, invoice_id: str) -> list[DecisionTrace]:
    """One invoice's decision trail, oldest first."""

    result = await session.execute(
        select(DecisionTrace)
        .where(DecisionTrace.invoice_id == invoice_id)
        .order_by(DecisionTrace.seq)
    )
    return list(result.scalars().all())


async def webhook_already_processed(session: AsyncSession, event_id: str) -> bool:
    """Whether this Razorpay event id has been seen before.

    Razorpay retries on any non-2xx response, so the same ``payment.captured``
    can arrive several times. Counting one payment twice would corrupt every
    recovery number downstream, which is why this check exists before any
    handler runs rather than inside one.
    """

    result = await session.execute(select(WebhookEvent.id).where(WebhookEvent.event_id == event_id))
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Inbound replies and promises
# ---------------------------------------------------------------------------


async def get_inbound_reply(session: AsyncSession, reply_id: str) -> InboundReply | None:
    """One stored reply by its provider message id, the idempotency key."""

    result = await session.execute(select(InboundReply).where(InboundReply.reply_id == reply_id))
    return result.scalar_one_or_none()


async def get_customer_by_email(session: AsyncSession, email: str) -> Customer | None:
    """Match a sender address to a customer, case-insensitively.

    Only ever used as a *secondary* signal: the tagged reply-to address is what
    identifies the invoice. This fills in who wrote, so an unmatched reply
    still lands in the review queue attached to the right customer.
    """

    if not email:
        return None
    result = await session.execute(
        select(Customer).where(func.lower(Customer.email) == email.strip().lower())
    )
    return result.scalars().first()


async def record_promise(
    session: AsyncSession,
    invoice: Invoice,
    promise: PromiseRecord,
) -> Promise:
    """Persist a promise extracted from a reply.

    Any earlier pending promise on the same invoice is marked SUPERSEDED rather
    than deleted or left open. Two live promises on one invoice would make
    "did they keep it?" unanswerable, and the old one is still the record of
    what the customer said last time.
    """

    existing = await session.execute(
        select(Promise).where(
            Promise.invoice_pk == invoice.id, Promise.status == PromiseStatus.PENDING
        )
    )
    for stale in existing.scalars().all():
        stale.status = PromiseStatus.SUPERSEDED
        stale.resolved_at = utc_now()

    row = Promise(
        promise_id=promise.promise_id,
        invoice_pk=invoice.id,
        promised_amount=promise.promised_amount,
        promised_date=promise.promised_date,
        currency=promise.currency,
        source_reply_id=promise.source_reply_id,
        source_confidence=promise.source_confidence,
        status=PromiseStatus.PENDING,
    )
    session.add(row)

    # A live promise is why the policy gate goes quiet on this invoice; the
    # status change is what the gate reads.
    if invoice.status in (InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS):
        invoice.status = InvoiceStatus.PROMISED

    await session.flush()
    return row


async def replies_needing_review(session: AsyncSession, *, limit: int = 100) -> list[InboundReply]:
    """The human review queue, oldest first.

    Oldest first on purpose: a queue worked newest-first leaves its oldest
    items to rot, and the oldest unreviewed reply is the one most likely to be
    a customer waiting on an answer.
    """

    result = await session.execute(
        select(InboundReply)
        .where(InboundReply.disposition == ReplyDisposition.NEEDS_REVIEW)
        .order_by(InboundReply.received_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def pending_promises(
    session: AsyncSession, *, limit: int = 500
) -> list[tuple[Promise, Invoice]]:
    """Every still-open promise with its invoice, oldest promised date first.

    Joined rather than fetched separately because the sweep needs
    ``invoice.amount_paid`` for each one, and a promise is only ever settled by
    money that actually arrived.
    """

    result = await session.execute(
        select(Promise, Invoice)
        .join(Invoice, Invoice.id == Promise.invoice_pk)
        .where(Promise.status == PromiseStatus.PENDING)
        .order_by(Promise.promised_date)
        .limit(limit)
    )
    return list(result.all())  # type: ignore[arg-type]


async def save_batch_run(session: AsyncSession, record: BatchRunRecord) -> BatchRunRecord:
    """Save an autonomous batch run record and its invoice decisions."""
    session.add(record)
    await session.flush()
    return record


async def get_latest_batch_run(session: AsyncSession) -> BatchRunRecord | None:
    """Get the most recent completed batch run."""
    result = await session.execute(
        select(BatchRunRecord).order_by(BatchRunRecord.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def list_batch_runs(session: AsyncSession, *, limit: int = 20) -> list[BatchRunRecord]:
    """Get list of past batch runs, most recent first."""
    result = await session.execute(
        select(BatchRunRecord).order_by(BatchRunRecord.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_batch_run_by_id(session: AsyncSession, run_id: str) -> BatchRunRecord | None:
    """Get a specific batch run by its run_id."""
    result = await session.execute(select(BatchRunRecord).where(BatchRunRecord.run_id == run_id))
    return result.scalar_one_or_none()
