"""Invoice endpoints: ingest, read, audit trail, and run one decision cycle."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent import AgentConfig, run_cycle
from app.core.audit import DecisionLedger, DecisionTraceEntry, append_decision_trace
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.policy import policy_config_from_settings
from app.db.session import get_db
from app.models import Customer, DecisionOutcome, DeliveryStatus, Invoice
from app.schemas import (
    AuditTrailOut,
    BatchIngestRequest,
    BatchIngestResponse,
    DecisionTraceOut,
    ExecutionOut,
    InvoiceOut,
    PolicyDecisionOut,
    PromiseOut,
    RunCycleResponse,
)
from app.services import repository
from app.services.executor import ExecutionIntent, build_execution_service
from src.ml.versioning import utc_now

router = APIRouter(prefix="/invoices", tags=["invoices"])
logger = get_logger(__name__)


@router.post("/batch", response_model=BatchIngestResponse, status_code=status.HTTP_201_CREATED)
async def ingest_batch(
    payload: BatchIngestRequest, db: AsyncSession = Depends(get_db)
) -> BatchIngestResponse:
    """Ingest customers and invoices.

    Idempotent by business key: re-posting the same batch skips what already
    exists rather than failing or duplicating. Demos get re-run, and a demo
    that only works on a clean database is a demo that will fail on stage.
    """

    response = BatchIngestResponse()

    for incoming in payload.customers:
        if await repository.get_customer(db, incoming.customer_id) is not None:
            response.customers_skipped += 1
            continue
        db.add(Customer(**incoming.model_dump()))
        response.customers_created += 1
    await db.flush()

    for incoming_invoice in payload.invoices:
        if await repository.get_invoice(db, incoming_invoice.invoice_id) is not None:
            response.invoices_skipped += 1
            continue

        customer = await repository.get_customer(db, incoming_invoice.customer_id)
        if customer is None:
            # Reported rather than raised: one unmatched invoice should not
            # discard an otherwise valid batch of several hundred.
            response.unknown_customer_ids.append(incoming_invoice.customer_id)
            continue

        fields = incoming_invoice.model_dump(exclude={"customer_id"})
        db.add(Invoice(customer_pk=customer.id, **fields))
        response.invoices_created += 1

    await db.commit()
    logger.info(
        "Batch ingested",
        customers_created=response.customers_created,
        invoices_created=response.invoices_created,
        unknown_customers=len(response.unknown_customer_ids),
    )
    return response


async def _load_invoice_or_404(db: AsyncSession, invoice_id: str) -> Invoice:
    invoice = await repository.get_invoice(db, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invoice {invoice_id} not found")
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(invoice_id: str, db: AsyncSession = Depends(get_db)) -> InvoiceOut:
    """One invoice's current state, including its promises."""

    invoice = await _load_invoice_or_404(db, invoice_id)
    customer = await db.get(Customer, invoice.customer_pk)
    if customer is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Invoice has no customer")

    today = utc_now().date()
    promises = [
        PromiseOut(
            promise_id=promise.promise_id,
            promised_amount=promise.promised_amount,
            promised_date=promise.promised_date,
            currency=promise.currency,
            status=promise.status.value,
            created_at=promise.created_at,
            resolved_at=promise.resolved_at,
        )
        for promise in invoice.promises
    ]

    return InvoiceOut(
        invoice_id=invoice.invoice_id,
        customer_id=customer.customer_id,
        customer_name=customer.name,
        amount=invoice.amount,
        amount_paid=invoice.amount_paid,
        outstanding=max(invoice.amount - invoice.amount_paid, 0.0),
        currency=invoice.currency,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        days_overdue=max((today - invoice.due_date).days, 0),
        status=invoice.status,
        escalation_state=invoice.escalation_state,
        ladder_index=invoice.ladder_index,
        prior_reminders_sent=invoice.prior_reminders_sent,
        last_contact_at=invoice.last_contact_at,
        payment_link_url=invoice.payment_link_url,
        paid_at=invoice.paid_at,
        promises=promises,
    )


@router.get("/{invoice_id}/audit", response_model=AuditTrailOut)
async def get_audit_trail(invoice_id: str, db: AsyncSession = Depends(get_db)) -> AuditTrailOut:
    """The full decision trail for one invoice.

    ``chain_verified`` re-derives each entry's hash from its stored content
    rather than trusting the stored hash. An audit trail that cannot say
    whether it has been tampered with is not an audit trail.
    """

    await _load_invoice_or_404(db, invoice_id)
    rows = await repository.trace_for_invoice(db, invoice_id)

    # Re-derive each entry's hash from its stored content and compare. Note
    # this checks *content integrity* per entry, not chain continuity: these
    # rows are one invoice's slice of a global ledger, so consecutive rows here
    # are not consecutive in the chain and their prev_hash values legitimately
    # point at other invoices' entries. Whole-chain continuity is checked over
    # the full ledger by DecisionLedger.verify.
    verified = all(
        DecisionTraceEntry(
            seq=row.seq,
            invoice_id=row.invoice_id,
            event=row.event,
            outcome=row.outcome,
            reason=row.reason,
            actor=row.actor,
            payload=row.payload,
            recorded_at=row.recorded_at,
            prev_hash=row.prev_hash,
        ).content_digest()
        == row.entry_hash
        for row in rows
    )

    return AuditTrailOut(
        invoice_id=invoice_id,
        entries=[
            DecisionTraceOut(
                seq=row.seq,
                event=row.event,
                outcome=row.outcome.value,
                reason=row.reason,
                actor=row.actor,
                payload=row.payload,
                recorded_at=row.recorded_at,
                prev_hash=row.prev_hash,
                entry_hash=row.entry_hash,
            )
            for row in rows
        ],
        entry_count=len(rows),
        chain_verified=verified,
    )


@router.post("/{invoice_id}/run-cycle", response_model=RunCycleResponse)
async def trigger_cycle(invoice_id: str, db: AsyncSession = Depends(get_db)) -> RunCycleResponse:
    """Run one decision cycle over this invoice, and act on it.

    Everything the agent decided is returned, including the cases where it
    decided to do nothing and why. This endpoint is the demo's centrepiece: it
    is the one place a judge can watch a single invoice go through
    score -> propose -> gate -> transition -> execute and read the reason at
    each step.

    The ``execute`` step is the one with a failure mode worth stating. A
    delivered message advances the ladder and consumes a contact slot; a failed
    one advances nothing, so the next cycle retries the same rung rather than
    marching the invoice toward final notice on the strength of emails that
    never arrived. ``execution`` in the response says which happened, and
    whether anything was really sent or only simulated under ``DRY_RUN``.
    """

    invoice = await _load_invoice_or_404(db, invoice_id)
    case = await repository.load_case(db, invoice)

    ledger = DecisionLedger()
    result = run_cycle(
        case,
        config=AgentConfig(policy=policy_config_from_settings()),
        ledger=ledger,
    )

    # --- execute ---------------------------------------------------------
    #
    # The ordering below is the whole point of this endpoint's rewrite. It used
    # to advance the ladder and write a ContactLog row unconditionally, for a
    # message nothing had sent. Now: send first, and only a delivered message
    # buys a rung.
    # Only a *contacting* action executes. HAND_OFF and CLOSE are approved
    # actions too, but they move internal state -- handing a case to a human is
    # not something the customer is emailed about. ExecutionIntent refuses them
    # outright, so the filter belongs here rather than being discovered there.
    execution = None
    halted = not get_settings().SENDING_ENABLED
    if (
        result.acted
        and result.action is not None
        and result.decision is not None
        and result.action.is_contact
        and halted
    ):
        # The kill switch, honoured here as well as in the batch runner: a
        # switch that only stops the scheduled path is not a kill switch.
        # Nothing sent and nothing advanced, so flipping it back on resumes
        # where the agent left off.
        append_decision_trace(
            invoice_id=invoice.invoice_id,
            event="execution:halted",
            outcome=DecisionOutcome.SKIPPED,
            reason="SENDING_ENABLED is false; outbound contact is halted.",
            ledger=ledger,
            ladder_step=result.ladder_step,
        )
    elif (
        result.acted
        and result.action is not None
        and result.decision is not None
        and result.action.is_contact
    ):
        intent = ExecutionIntent.from_decision(case, result.action, result.decision)
        executor = build_execution_service()
        execution = await executor.execute(db, intent, invoice)

        append_decision_trace(
            invoice_id=invoice.invoice_id,
            event="executed",
            outcome=(DecisionOutcome.EXECUTED if execution.delivered else DecisionOutcome.FAILED),
            reason=(
                f"{execution.status.value} via {execution.channel.value}"
                if execution.delivered
                else f"delivery failed: {execution.error}"
            ),
            ledger=ledger,
            ladder_step=execution.ladder_step,
            provider_message_id=execution.provider_message_id,
            payment_link_id=execution.payment_link_id,
            amount_requested=execution.amount_requested,
        )

    # A transition that produced no delivered message must not move the case.
    # Burning a rung on a failed send walks an invoice to final notice without
    # the customer ever hearing from us; the next cycle should retry this rung.
    advanced = result.transitioned and not halted and (execution is None or execution.delivered)
    if advanced:
        invoice.escalation_state = result.state_after
        invoice.ladder_index += 1

    await repository.persist_ledger(db, ledger)
    await db.commit()

    decision = None
    if result.decision is not None:
        decision = PolicyDecisionOut(
            allowed=result.decision.allowed,
            reason=result.decision.reason,
            violations=[
                {"code": violation.code, "message": violation.message}
                for violation in result.decision.violations
            ],
            adjustments=[adjustment.model_dump() for adjustment in result.decision.adjustments],
            effective_discount_pct=result.decision.effective_discount_pct,
            effective_discount_amount=result.decision.effective_discount_amount,
        )

    return RunCycleResponse(
        invoice_id=result.invoice_id,
        tier=result.tier,
        p_recovery=result.score.p_recovery,
        expected_value=result.score.expected_value,
        outstanding=result.score.outstanding,
        rationale=result.score.rationale,
        top_drivers=[driver.model_dump() for driver in result.score.prediction.top_drivers],
        action_type=result.action.action_type.value if result.action else None,
        ladder_step=result.ladder_step,
        decision=decision,
        # Reports what the *database* now says, not what the FSM proposed: a
        # failed send leaves the case where it was.
        transitioned=advanced,
        state_before=result.state_before,
        state_after=(result.state_after if advanced else result.state_before),
        reason=result.reason,
        terminal=result.terminal,
        scorer_fallback=result.score.prediction.fallback_used,
        scorer_version=result.score.prediction.model_version,
        execution=(
            ExecutionOut(
                status=execution.status.value,
                delivered=execution.delivered,
                dry_run=execution.status is DeliveryStatus.SIMULATED,
                subject=execution.subject,
                body_preview=execution.body_preview,
                provider_message_id=execution.provider_message_id,
                payment_link_id=execution.payment_link_id,
                payment_link_url=execution.payment_link_url,
                payment_link_reused=execution.payment_link_reused,
                amount_requested=execution.amount_requested,
                error=execution.error,
            )
            if execution is not None
            else None
        ),
    )
