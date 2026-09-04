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
from app.models import DecisionOutcome, InvoiceStatus, PromiseStatus, WebhookEvent
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

    throttled = await limited_429_response(
        request, get_settings().RATE_LIMIT_PER_MINUTE, "razorpay-webhook"
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

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "rejected", "reason": "malformed_json"},
        )

    event_id = x_razorpay_event_id or _extract_event_id(payload, raw)
    event_type = str(payload.get("event", "unknown"))

    if await repository.webhook_already_processed(db, event_id):
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
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        signature_verified=True,
    )
    db.add(event)

    ledger = DecisionLedger()
    try:
        handled = await _handle_event(db, event_type, payload, ledger)
        event.processed_at = utc_now()
    except Exception as exc:  # noqa: BLE001 - stored, acknowledged, investigated
        # See property 3 in the module docstring: we keep the event and return
        # 200 so Razorpay stops retrying, and carry the error for a human.
        event.processing_error = f"{type(exc).__name__}: {exc}"
        logger.error("Webhook handler failed", event_id=event_id, error=str(exc))
        handled = False

    await repository.persist_ledger(db, ledger)
    await db.commit()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "processed" if handled else "stored", "event_id": event_id},
    )


async def _handle_event(
    db: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    ledger: DecisionLedger,
) -> bool:
    """Apply one verified event. Returns whether a handler ran."""

    if event_type not in HANDLED_EVENTS:
        return False

    entity = _payment_entity(payload)
    payment_link_id = entity.get("payment_link_id") or entity.get("id")
    if not isinstance(payment_link_id, str):
        return False

    invoice = await repository.get_invoice_by_payment_link(db, payment_link_id)
    if invoice is None:
        logger.warning("Webhook for an unknown payment link", payment_link_id=payment_link_id)
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

    invoice.amount_paid += amount
    if invoice.amount_paid + 0.01 >= invoice.amount:
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = utc_now()

    append_decision_trace(
        invoice_id=invoice.invoice_id,
        event="payment:received",
        outcome=DecisionOutcome.EXECUTED,
        reason=(
            f"Payment of {invoice.currency} {amount:,.2f} confirmed by Razorpay; "
            f"{invoice.amount_paid:,.2f} of {invoice.amount:,.2f} settled."
        ),
        ledger=ledger,
        payment_link_id=payment_link_id,
        amount=amount,
        invoice_status=invoice.status.value,
    )

    # A payment is the only thing that can settle a promise. Assess any open
    # one against what actually arrived.
    promise_row = await repository.open_promise_for(db, invoice.id)
    if promise_row is not None:
        from app.core.promise_tracker import PromiseRecord

        record = PromiseRecord(
            promise_id=promise_row.promise_id,
            invoice_id=invoice.invoice_id,
            promised_amount=promise_row.promised_amount,
            promised_date=promise_row.promised_date,
            currency=promise_row.currency,
        )
        outcome = assess_promise(record, amount_paid=invoice.amount_paid, ledger=ledger)
        if outcome.status is PromiseStatus.KEPT:
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
