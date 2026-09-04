"""Inbound replies: the half of Promise-to-Pay that listens.

The agent could send and could classify, but nothing connected a real customer
reply to either. This endpoint closes that loop:

    verify signature -> dedupe -> route to invoice -> classify -> act

Four properties hold, and each exists because its absence is a specific
failure:

**Signature first, before the body is trusted for anything.** The payload names
an invoice and can create a promise against it. An unverified endpoint is an
endpoint where anyone can mark any debt as promised and stop the agent chasing
it.

**Deduped on the provider's message id**, through the same ``webhook_events``
row Razorpay's handler uses. Resend retries on non-2xx, and a retried delivery
must not record the promise twice.

**Routed by a signed reply-to address, never the subject line.** Subjects get
edited, forwarded and truncated; an address minted and signed by
``app.services.reply_routing`` cannot be pointed at someone else's invoice.

**Low confidence goes to a person.** A classifier that is unsure writes a
``NEEDS_REVIEW`` row and no promise. The two mistakes are not symmetric:
inventing a commitment the customer never made stops the agent chasing a live
debt, which is worse than making someone read an email.

Opt-out is the one intent handled ahead of all of that. A request to stop is
binding regardless of what else the classifier thought, and it is honoured even
when the reply could not be matched to an invoice.
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
from app.core.promise_tracker import PromiseRecord, extract_promise
from app.core.ratelimit import limited_429_response
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import (
    Customer,
    DecisionOutcome,
    InboundReply,
    Invoice,
    ReplyDisposition,
    WebhookEvent,
)
from app.schemas.models import (
    ClassifyPreviewEntities,
    ClassifyPreviewIn,
    ClassifyPreviewOut,
)
from app.schemas.replies import ReplyIngestResponse, ReplyReviewItem, ReplyReviewQueue
from app.services import repository
from app.services.reply_routing import resolve_from_recipients, verify_svix_signature
from src.ml.config import MLSettings
from src.ml.reply.service import looks_like_opt_out, understand_reply
from src.ml.schemas import IntentLabel
from src.ml.versioning import utc_now

router = APIRouter(prefix="/replies", tags=["replies"])
logger = get_logger(__name__)
settings = get_settings()

#: Intents that end the conversation rather than continuing it.
_OPT_OUT_INTENTS = frozenset({IntentLabel.OPT_OUT})


def _extract_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the message out of Resend's envelope.

    Resend nests the message under ``data``; a hand-posted test payload is
    usually flat. Accepting both keeps the local harness honest without a
    second code path.
    """

    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _recipients(message: dict[str, Any]) -> list[str]:
    """Every address the reply was addressed to, ``to`` and ``cc`` alike."""

    found: list[str] = []
    for key in ("to", "cc"):
        value = message.get(key)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(item) for item in value)
    return found


def _body_text(message: dict[str, Any]) -> str:
    """The reply's text, preferring plain text over HTML."""

    for key in ("text", "plain", "body", "html"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


@router.post("", status_code=status.HTTP_200_OK)
async def receive_reply(
    request: Request,
    db: AsyncSession = Depends(get_db),
    svix_id: str = Header(default="", alias="svix-id"),
    svix_timestamp: str = Header(default="", alias="svix-timestamp"),
    svix_signature: str = Header(default="", alias="svix-signature"),
    x_worker_secret: str = Header(default="", alias="x-worker-secret"),
) -> JSONResponse:
    """Receive one inbound customer reply from Cloudflare Worker or Resend."""

    throttled = await limited_429_response(
        request, get_settings().RATE_LIMIT_PER_MINUTE, "reply-webhook"
    )
    if throttled is not None:
        return throttled

    raw = await request.body()
    active_settings = get_settings()

    verified, reason = verify_svix_signature(
        body=raw,
        message_id=svix_id,
        timestamp=svix_timestamp,
        signature_header=svix_signature,
        secret=active_settings.RESEND_WEBHOOK_SECRET,
    )
    if not verified and x_worker_secret and active_settings.RESEND_WEBHOOK_SECRET:
        import hmac

        if hmac.compare_digest(x_worker_secret, active_settings.RESEND_WEBHOOK_SECRET):
            verified = True
            reason = "ok (worker secret header)"

    if not verified:
        # The reason is logged, never returned: telling an unauthenticated
        # caller *why* their forgery failed helps them make a better one.
        logger.warning("Rejected inbound reply", reason=reason, bytes=len(raw))
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

    message = _extract_message(payload)
    reply_id = str(message.get("message_id") or message.get("id") or svix_id)
    if not reply_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "rejected", "reason": "no_message_id"},
        )

    existing = await repository.get_inbound_reply(db, reply_id)
    if existing is not None:
        logger.info("Duplicate reply ignored", reply_id=reply_id)
        return JSONResponse(
            content={
                "status": "duplicate",
                "reply_id": reply_id,
                "disposition": existing.disposition.value,
            }
        )

    # Mirrored into webhook_events for the same reason Razorpay's are: one
    # place to answer "did this delivery reach us, and what did we do".
    db.add(
        WebhookEvent(
            event_id=f"resend:{reply_id}",
            event_type="email.inbound",
            payload=payload,
            signature_verified=True,
        )
    )

    from_email = str(message.get("from") or "")
    body = _body_text(message)
    recipients = _recipients(message)

    invoice_id = resolve_from_recipients(recipients, secret=active_settings.REPLY_ADDRESS_SECRET)
    invoice: Invoice | None = await repository.get_invoice(db, invoice_id) if invoice_id else None
    customer: Customer | None = None
    if invoice is not None:
        customer = await db.get(Customer, invoice.customer_pk)
    if customer is None:
        customer = await repository.get_customer_by_email(db, from_email)

    reply = InboundReply(
        reply_id=reply_id,
        invoice_pk=invoice.id if invoice else None,
        customer_pk=customer.id if customer else None,
        from_email=from_email[:320],
        to_email=(recipients[0] if recipients else "")[:320],
        subject=str(message.get("subject") or "")[:255],
        body=body,
    )
    db.add(reply)

    ledger = DecisionLedger()
    outcome = await _process(db, reply, invoice, customer, body, ledger)

    await repository.persist_ledger(db, ledger)
    await db.commit()

    return JSONResponse(content=outcome.model_dump())


async def _process(
    db: AsyncSession,
    reply: InboundReply,
    invoice: Invoice | None,
    customer: Customer | None,
    body: str,
    ledger: DecisionLedger,
) -> ReplyIngestResponse:
    """Classify one reply and act on it. Never raises."""

    ml_settings = MLSettings()

    # --- opt-out, ahead of everything -------------------------------------
    #
    # Checked before classification and honoured without an invoice. Someone
    # who asked to be left alone must be left alone even if we cannot work out
    # which debt they were writing about.
    if looks_like_opt_out(body):
        reply.intent = IntentLabel.OPT_OUT.value
        reply.confidence = 1.0
        reply.classifier_version = "opt-out-guard"
        if customer is not None:
            await repository.record_opt_out(
                db,
                customer,
                reason="Customer asked to stop receiving reminders.",
                source_reply_id=reply.reply_id,
                honour_days=settings.DEFAULT_OPTOUT_DAYS,
            )
            reply.disposition = ReplyDisposition.AUTO_HANDLED
            reply.disposition_reason = "Opt-out recorded; contact suppressed."
            append_decision_trace(
                invoice_id=invoice.invoice_id if invoice else reply.reply_id,
                event="reply:opt_out",
                outcome=DecisionOutcome.EXECUTED,
                reason="Customer asked to stop; opt-out registered.",
                ledger=ledger,
                reply_id=reply.reply_id,
            )
        else:
            reply.disposition = ReplyDisposition.NEEDS_REVIEW
            reply.disposition_reason = (
                "Opt-out request from an address we cannot match to a customer."
            )
        return ReplyIngestResponse(
            status="processed",
            reply_id=reply.reply_id,
            intent=reply.intent,
            confidence=reply.confidence,
            disposition=reply.disposition.value,
            reason=reply.disposition_reason,
        )

    # --- unroutable -------------------------------------------------------
    if invoice is None:
        reply.disposition = ReplyDisposition.NEEDS_REVIEW
        reply.disposition_reason = (
            "Could not match this reply to an invoice: no valid tagged reply-to "
            "address. Queued rather than discarded."
        )
        logger.info("Reply could not be routed", reply_id=reply.reply_id)
        return ReplyIngestResponse(
            status="queued",
            reply_id=reply.reply_id,
            disposition=reply.disposition.value,
            reason=reply.disposition_reason,
        )

    # --- classify ---------------------------------------------------------
    prediction = await understand_reply(
        body,
        invoice.invoice_id,
        reply.reply_id,
        invoice_context={
            "invoice_id": invoice.invoice_id,
            "amount": invoice.amount,
            "outstanding": max(invoice.amount - invoice.amount_paid, 0.0),
            "due_date": invoice.due_date.isoformat(),
            "currency": invoice.currency,
        },
        settings=ml_settings,
    )

    reply.intent = prediction.intent.value
    reply.confidence = prediction.confidence
    reply.classifier_version = prediction.model_version
    reply.fallback_used = prediction.fallback_used

    append_decision_trace(
        invoice_id=invoice.invoice_id,
        event="reply:classified",
        outcome=DecisionOutcome.APPROVED,
        reason=f"Classified as {prediction.intent.value} at {prediction.confidence:.2f}.",
        ledger=ledger,
        reply_id=reply.reply_id,
        intent=prediction.intent.value,
        confidence=prediction.confidence,
        classifier_version=prediction.model_version,
        fallback_used=prediction.fallback_used,
    )

    if prediction.intent in _OPT_OUT_INTENTS and customer is not None:
        await repository.record_opt_out(
            db,
            customer,
            reason="Classifier read this reply as an unsubscribe request.",
            source_reply_id=reply.reply_id,
            honour_days=settings.DEFAULT_OPTOUT_DAYS,
        )
        reply.disposition = ReplyDisposition.AUTO_HANDLED
        reply.disposition_reason = "Opt-out recorded; contact suppressed."
        return ReplyIngestResponse(
            status="processed",
            reply_id=reply.reply_id,
            intent=reply.intent,
            confidence=reply.confidence,
            classifier_version=reply.classifier_version,
            disposition=reply.disposition.value,
            reason=reply.disposition_reason,
        )

    # --- confidence gate --------------------------------------------------
    if prediction.fallback_used or prediction.confidence < ml_settings.ml_confidence_threshold:
        reply.disposition = ReplyDisposition.NEEDS_REVIEW
        reply.disposition_reason = (
            f"Confidence {prediction.confidence:.2f} is below the "
            f"{ml_settings.ml_confidence_threshold:.2f} threshold"
            + (" (classifier fell back)" if prediction.fallback_used else "")
            + "; routed to a human rather than guessed."
        )
        append_decision_trace(
            invoice_id=invoice.invoice_id,
            event="reply:needs_review",
            outcome=DecisionOutcome.SKIPPED,
            reason=reply.disposition_reason,
            ledger=ledger,
            reply_id=reply.reply_id,
            confidence=prediction.confidence,
        )
        return ReplyIngestResponse(
            status="queued",
            reply_id=reply.reply_id,
            intent=reply.intent,
            confidence=reply.confidence,
            classifier_version=reply.classifier_version,
            disposition=reply.disposition.value,
            reason=reply.disposition_reason,
        )

    # --- promise ----------------------------------------------------------
    outstanding = max(invoice.amount - invoice.amount_paid, 0.0)
    extracted = extract_promise(prediction, invoice_amount=outstanding, ledger=ledger)

    if isinstance(extracted, PromiseRecord):
        row = await repository.record_promise(db, invoice, extracted)
        reply.promise_pk = row.id
        reply.disposition = ReplyDisposition.AUTO_HANDLED
        reply.disposition_reason = (
            f"Promise of {extracted.currency} {extracted.promised_amount:,.2f} "
            f"by {extracted.promised_date.isoformat()} recorded."
        )
        return ReplyIngestResponse(
            status="processed",
            reply_id=reply.reply_id,
            intent=reply.intent,
            confidence=reply.confidence,
            classifier_version=reply.classifier_version,
            disposition=reply.disposition.value,
            reason=reply.disposition_reason,
            promise_id=extracted.promise_id,
            promised_amount=extracted.promised_amount,
            promised_date=extracted.promised_date.isoformat(),
        )

    # extract_promise declined, and has already written the reason to the
    # trace. A confidently-classified reply that is simply not a promise --
    # a dispute, a question -- is handled, not queued; one the extractor
    # could not turn into a trackable commitment goes to a person.
    routine = extracted.code == "intent_not_a_promise"
    reply.disposition = ReplyDisposition.AUTO_HANDLED if routine else ReplyDisposition.NEEDS_REVIEW
    reply.disposition_reason = extracted.message
    return ReplyIngestResponse(
        status="processed" if routine else "queued",
        reply_id=reply.reply_id,
        intent=reply.intent,
        confidence=reply.confidence,
        classifier_version=reply.classifier_version,
        disposition=reply.disposition.value,
        reason=extracted.message,
    )


@router.post(
    "/classify-preview", response_model=ClassifyPreviewOut, dependencies=[Depends(require_api_key)]
)
async def classify_preview(payload: ClassifyPreviewIn) -> ClassifyPreviewOut:
    """Classify one reply text without any side effects.

    A thin wrapper around the same ``understand_reply`` path the ingestion
    pipeline uses -- same cascade, same guard, same extractors -- minus
    everything mutating: no signature verification, no DB write, no promise
    creation, no opt-out recorded. Built for the Reply Understanding Studio's
    try-it-yourself box, where a judge mashing the button must never move
    money, create records, or silence a real customer.
    """

    import uuid

    from src.ml.reply.classifier import MODEL_NAME as STAGE_C_NAME
    from src.ml.schemas import FallbackResolver

    text = payload.text.strip()
    prediction = await understand_reply(
        text,
        payload.invoice_id,
        f"preview-{uuid.uuid4().hex[:8]}",
        invoice_context={"invoice_id": payload.invoice_id},
        settings=MLSettings(),
    )

    if looks_like_opt_out(text):
        stage_used = "guard"
    elif not prediction.fallback_used and prediction.model_used == STAGE_C_NAME:
        stage_used = "cascade"
    else:
        stage_used = "llm"

    entities = prediction.entities
    # The guard is the decision, not a suggestion: a matched opt-out is binding
    # even when the classifier underneath fell back, mirroring ingestion where
    # the guard path records the opt-out without consulting confidence.
    needs_review = (
        False
        if stage_used == "guard"
        else bool(
            prediction.fallback_used
            and prediction.fallback is not None
            and prediction.fallback.resolved_by is FallbackResolver.HUMAN_REVIEW_QUEUE
        )
    )

    return ClassifyPreviewOut(
        intent=prediction.intent.value,
        confidence=prediction.confidence,
        entities=ClassifyPreviewEntities(
            promised_amount=entities.promised_amount,
            promised_date=entities.promised_date.isoformat()
            if entities.promised_date is not None
            else None,
            currency=entities.currency,
            dispute_reason=entities.dispute_reason,
        ),
        stage_used=stage_used,
        classifier_version=prediction.model_version,
        fallback_used=prediction.fallback_used,
        needs_review=needs_review,
        explanation=prediction.explanation,
    )


@router.get("/review", response_model=ReplyReviewQueue, dependencies=[Depends(require_api_key)])
@router.get("/review-queue", response_model=ReplyReviewQueue, dependencies=[Depends(require_api_key)])
async def review_queue(limit: int = 50, db: AsyncSession = Depends(get_db)) -> ReplyReviewQueue:
    """Replies waiting on a person, oldest first.

    This endpoint is what makes "routed to a human" a real destination rather
    than a phrase in a docstring.
    """

    rows = await repository.replies_needing_review(db, limit=limit)
    return ReplyReviewQueue(
        count=len(rows),
        items=[
            ReplyReviewItem(
                reply_id=row.reply_id,
                from_email=row.from_email,
                subject=row.subject,
                body=row.body[:1000],
                intent=row.intent or None,
                confidence=row.confidence,
                classifier_version=row.classifier_version or None,
                reason=row.disposition_reason,
                received_at=row.received_at,
            )
            for row in rows
        ],
    )


@router.post(
    "/{reply_id}/reviewed",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_api_key)],
)
async def mark_reviewed(reply_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Take one reply off the queue once a person has dealt with it."""

    reply = await repository.get_inbound_reply(db, reply_id)
    if reply is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"status": "not_found", "reply_id": reply_id},
        )

    reply.disposition = ReplyDisposition.REVIEWED
    reply.reviewed_at = utc_now()
    await db.commit()
    return JSONResponse(content={"status": "reviewed", "reply_id": reply_id})
