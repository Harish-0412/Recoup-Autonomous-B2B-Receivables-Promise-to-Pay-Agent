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

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import DecisionLedger
from app.core.domain import CaseSnapshot, snapshot_from_rows
from app.models import (
    ContactChannel,
    ContactLog,
    Customer,
    DecisionTrace,
    Invoice,
    InvoiceStatus,
    OptOut,
    Promise,
    PromiseStatus,
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
            )
        )
        .order_by(Invoice.due_date)
        .limit(limit)
    )
    return list(result.scalars().all())


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


async def contacts_sent_count(session: AsyncSession, invoice_pk: int) -> int:
    """How many messages have gone out about this invoice."""

    result = await session.execute(
        select(func.count()).select_from(ContactLog).where(ContactLog.invoice_pk == invoice_pk)
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
    provider_message_id: str | None = None,
) -> ContactLog:
    """Log an outbound message and update the invoice's contact counters.

    Both happen together deliberately: the frequency cap counts ``ContactLog``
    rows and the gap rule reads ``last_contact_at``, so a send recorded in one
    place but not the other would quietly widen a cap.
    """

    contact = ContactLog(
        invoice_pk=invoice.id,
        channel=channel,
        ladder_step=ladder_step,
        subject=subject,
        body_preview=body_preview[:500],
        provider_message_id=provider_message_id,
    )
    session.add(contact)

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

    The hash chain is computed in the core and stored as-is; this function does
    not recompute it. ``seq`` continues from what is already persisted so the
    chain stays dense across process restarts.
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
