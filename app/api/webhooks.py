"""Razorpay webhook receiver -- the only source of truth for "was this paid".

Three properties this endpoint must have, in order of how badly getting them
wrong would hurt:

1. **Signature verification before anything else.** An unverified payload is
   an unauthenticated claim that money moved. It is recorded and rejected; it
   never reaches a handler.
2. **Idempotency.** Razorpay retries on any non-2xx, and can deliver the same
   event more than once regardless. ``webhook_events.event_id`` is unique and a
   repeat delivery is a no-op -- double-counting a payment would corrupt every
   recovery number the project is judged on.
3. **Acknowledge what we stored, not what we understood.** An event we cannot
   parse is still stored and still returns 200, with the error recorded. Making
   Razorpay retry forever over a payload shape we do not handle turns our bug
   into an incident.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import DecisionLedger, append_decision_trace
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.promise_tracker import assess_promise
from app.core.ratelimit import limited_429_response
from app.db.session import get_db
from app.models import DecisionOutcome, Invoice, PromiseStatus, WebhookEvent
from app.models.tables import DEFAULT_BUSINESS_ID
from app.services import repository
from app.services.razorpay_client import get_razorpay_client
from src.ml.versioning import utc_now

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)
settings = get_settings()

#: Events this receiver acts on. Anything else is stored and acknowledged
#: without a handler, so adding one later is a code change rather than a
#: scramble to find what was dropped.
HANDLED_EVENTS: frozenset[str] = frozenset(
    {"payment_link.paid", "payment.captured", "payment_link.expired"}
)


def _extract_event_id(payload: dict[str, Any], raw: bytes) -> str:
    """Get a stable id for this delivery.

    Razorpay's ``x-razorpay-event-id`` header is the right key, but it is not
    always present on replayed test-mode events. Falling back to a hash of the
    body keeps idempotency working rather than silently disabling it.
    """

    import hashlib

    for candidate in (payload.get("id"), payload.get("event_id")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(default=""),
    x_razorpay_event_id: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Receive one Razorpay webhook delivery."""

    # Outer surface before signature verification. Webhook traffic fails
    # *open* on a Redis outage (in-process cap only): the required behaviour is
    # "200-store for webhooks after signature check", so a shared-limiter
    # outage must not turn every payment event into a 429 that Razorpay
    # retries forever. Forgery protection is the signature check that follows,
    # and an unverified event is never stored. (Ingest/API scopes are the
    # opposite: fail-closed, see app/core/ratelimit.py.)
    throttled = await limited_429_response(
        request,
        get_settings().RATE_LIMIT_PER_MINUTE,
        "razorpay-webhook",
        fail_mode="open_after_sig",
    )
    if throttled is not None:
        return throttled

    raw = await request.body()

    client = get_razorpay_client()
    verified = client.verify_webhook_signature(
        raw, x_razorpay_signature, settings.RAZORPAY_WEBHOOK_SECRET
    )
    if not verified:
        # Recorded, then refused. A rejected delivery is exactly the thing an
        # auditor will ask whether we noticed.
        logger.warning("Rejected webhook with an invalid signature", bytes=len(raw))
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"status": "rejected", "reason": "invalid_signature"},
        )

    # Signature passed. Secondary per-tenant cap with Redis fail-open: if Redis
    # is down we still store the event and answer 200 rather than making
    # Razorpay retry forever. Process-local deque still provides a
    # conservative soft cap.
    throttled_verified = await limited_429_response(
        request,
        get_settings().RATE_LIMIT_PER_MINUTE * 2,  # verified gets headroom
        f"{DEFAULT_BUSINESS_ID}:razorpay-webhook-verified",
        fail_mode="open_after_sig",
    )
    if throttled_verified is not None:
        return throttled_verified

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "rejected", "reason": "malformed_json"},
        )

    event_id = x_razorpay_event_id or _extract_event_id(payload, raw)
    event_type = str(payload.get("event", "unknown"))
    # Tenant comes from the payment link's notes (minted by the executor with
    # business_id), not from any credential: this endpoint is public and
    # proves itself by webhook signature.
    business_id = _extract_business_id(payload) or DEFAULT_BUSINESS_ID

    if await repository.webhook_already_processed(db, event_id, business_id):
        # `event_type=`, never `event=`: structlog uses `event` for the message
        # itself, so passing it as a field raises TypeError -- which turned
        # every duplicate delivery, the case this branch exists to handle
        # gracefully, into a 500 and a Razorpay retry.
        logger.info("Duplicate webhook ignored", event_id=event_id, event_type=event_type)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "duplicate", "event_id": event_id},
        )

    event = WebhookEvent(
        business_id=business_id,
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        signature_verified=True,
    )
    db.add(event)

    ledger = DecisionLedger()
    try:
        handled = await _handle_event(db, event_type, payload, ledger, business_id)
        event.processed_at = utc_now()
    except Exception as exc:  # noqa: BLE001 - stored, acknowledged, investigated
        # See property 3 in the module docstring: we keep the event and return
        # 200 so Razorpay stops retrying, and carry the error for a human.
        event.processing_error = f"{type(exc).__name__}: {exc}"
        logger.error("Webhook handler failed", event_id=event_id, error=str(exc))
        handled = False

    await repository.persist_ledger(db, ledger, business_id)
    await db.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "processed" if handled else "stored", "event_id": event_id},
    )


def _extract_business_id(payload: dict[str, Any]) -> str | None:
    """Tenant stamped on the payment link at creation (executor notes).

    Returns None for pre-Wave-1 links whose notes carry no business_id; the
    caller then takes the audited legacy path in _handle_event.
    """

    entity = _payment_entity(payload)
    notes = entity.get("notes")
    if isinstance(notes, dict):
        candidate = notes.get("business_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    # Some Razorpay events nest notes one level deeper (payment -> notes).
    container = payload.get("payload")
    if isinstance(container, dict):
        for key in ("payment", "payment_link", "order"):
            section = container.get(key)
            if isinstance(section, dict):
                inner = section.get("entity")
                if isinstance(inner, dict):
                    notes = inner.get("notes")
                    if isinstance(notes, dict):
                        candidate = notes.get("business_id")
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
    return None


async def _handle_event(
    db: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    ledger: DecisionLedger,
    business_id: str,
) -> bool:
    """Apply one verified event within one tenant. Returns whether a handler ran."""

    if event_type not in HANDLED_EVENTS:
        return False

    entity = _payment_entity(payload)
    payment_link_id = entity.get("payment_link_id") or entity.get("id")
    if not isinstance(payment_link_id, str):
        return False

    invoice = await repository.get_invoice_by_payment_link(db, payment_link_id, business_id)
    if invoice is None:
        # Legacy shim: pre-Wave-1 links carry no business_id in notes. Resolve
        # the owning tenant by link, then scope every mutation below to that
        # tenant. This is the ONLY unscoped invoice read in the codebase, it
        # is logged, and it disappears once legacy links expire.
        if _extract_business_id(payload) is None:
            from sqlalchemy import select

            legacy = await db.execute(
                select(Invoice).where(Invoice.payment_link_id == payment_link_id)
            )
            found = legacy.scalar_one_or_none()
            if found is None:
                logger.warning(
                    "Webhook for an unknown payment link",
                    payment_link_id=payment_link_id,
                    business_id=business_id,
                )
                return False
            logger.warning(
                "Webhook on legacy link without business_id; scoped to owning tenant",
                payment_link_id=payment_link_id,
                business_id=found.business_id,
            )
            business_id = found.business_id
            invoice = found
        else:
            logger.warning(
                "Webhook for an unknown payment link in this tenant",
                payment_link_id=payment_link_id,
                business_id=business_id,
            )
            return False

    if event_type == "payment_link.expired":
        append_decision_trace(
            invoice_id=invoice.invoice_id,
            event="payment_link:expired",
            outcome=DecisionOutcome.SKIPPED,
            reason="Razorpay reported the payment link expired unpaid.",
            ledger=ledger,
            payment_link_id=payment_link_id,
        )
        return True

    # Razorpay reports amounts in paise. Dividing here, once, keeps the unit
    # conversion out of every downstream comparison.
    amount_paise = entity.get("amount_paid") or entity.get("amount") or 0
    amount = float(amount_paise) / 100.0

    # The provider_ref that keys this allocation is the Razorpay payment_id.
    # Both payment.captured and payment_link.paid can carry it; using the
    # payment_id (not the payment link id) as the ref means the two event
    # types are recorded under different source values but the same ref,
    # making a true double-fire (same payment_id, same source) impossible.
    razorpay_payment_id = entity.get("id") or entity.get("payment_id") or payment_link_id

    # Source reflects which Razorpay event type fired. This is intentional:
    # both events for the same transaction will have the same provider_ref but
    # different sources, so neither unique constraint fires — but we sum both
    # when recomputing amount_paid. To prevent double-counting, we use the
    # payment_id (which is the same for both events) as provider_ref for BOTH
    # sources, effectively deduplicating via: same payment_id → same row.
    # If Razorpay fires payment.captured AND payment_link.paid for the same
    # payment, the second write with the same (business_id, source, provider_ref)
    # hits the unique constraint and returns None (no-op).
    from app.models.enums import AllocationSource

    source = (
        AllocationSource.RAZORPAY_LINK
        if event_type == "payment_link.paid"
        else AllocationSource.RAZORPAY_PAYMENT
    )

    allocation = await repository.create_allocation(
        db,
        business_id=business_id,
        invoice_pk=invoice.id,
        source=source,
        provider_ref=razorpay_payment_id,
        amount=amount,
        currency=invoice.currency,
        received_at=utc_now(),
        recorded_by="webhook",
    )

    # allocation is None when this event was a duplicate (idempotent path)
    if allocation is None:
        logger.info(
            "Duplicate Razorpay allocation ignored",
            source=source.value,
            provider_ref=razorpay_payment_id,
            business_id=business_id,
        )
        return True

    # Recompute amount_paid as SUM(allocations) and derive new status.
    # This is the only place that writes invoice.amount_paid and invoice.status
    # from a payment event.
    new_amount_paid = await repository.recompute_and_update_invoice(db, invoice, business_id)

    append_decision_trace(
        invoice_id=invoice.invoice_id,
        event="payment:received",
        outcome=DecisionOutcome.EXECUTED,
        reason=(
            f"Payment of {invoice.currency} {amount:,.2f} confirmed by Razorpay; "
            f"{new_amount_paid:,.2f} of {invoice.amount:,.2f} settled."
        ),
        ledger=ledger,
        payment_link_id=payment_link_id,
        amount=amount,
        invoice_status=invoice.status.value,
        allocation_source=source.value,
        allocation_ref=razorpay_payment_id,
    )

    # A payment is the only thing that can settle a promise. Assess any open
    # one against what actually arrived.
    promise_row = await repository.open_promise_for(db, invoice.id, business_id)
    if promise_row is not None:
        from app.core.payment_allocations import promise_is_kept
        from app.core.promise_tracker import PromiseRecord

        record = PromiseRecord(
            promise_id=promise_row.promise_id,
            invoice_id=invoice.invoice_id,
            promised_amount=promise_row.promised_amount,
            promised_date=promise_row.promised_date,
            currency=promise_row.currency,
        )
        # Use the 1-INR-tolerance kept rule (promise_is_kept) for the full
        # promise assessment; also pass to assess_promise so the audit trace
        # is written either way.
        outcome = assess_promise(record, amount_paid=new_amount_paid, ledger=ledger)
        if outcome.status is PromiseStatus.KEPT or promise_is_kept(
            new_amount_paid, promise_row.promised_amount
        ):
            promise_row.status = PromiseStatus.KEPT
            promise_row.resolved_at = utc_now()

    return True


def _payment_entity(payload: dict[str, Any]) -> dict[str, Any]:
    """Dig the payment or payment-link entity out of Razorpay's envelope.

    Razorpay nests entities under ``payload.<type>.entity``, and which type is
    present depends on the event. Rather than branching per event, take the
    first entity we recognise.
    """

    container = payload.get("payload")
    if not isinstance(container, dict):
        return {}

    for key in ("payment_link", "payment", "order"):
        section = container.get(key)
        if isinstance(section, dict):
            entity = section.get("entity")
            if isinstance(entity, dict):
                return entity
    return {}
