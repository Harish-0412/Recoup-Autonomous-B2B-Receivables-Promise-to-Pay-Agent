"""Invoice endpoints: ingest, read, audit trail, and run one decision cycle."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent import AgentConfig, run_batch, run_cycle
from app.core.audit import DecisionLedger, DecisionTraceEntry, append_decision_trace
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.policy import policy_config_from_settings
from app.core.ratelimit import rate_limit
from app.core.security import require_api_key
from app.db.session import get_db
from app.models import (
    Customer,
    DecisionOutcome,
    DeliveryStatus,
    EscalationState,
    InterventionTier,
    Invoice,
    InvoiceStatus,
)
from app.schemas import (
    AuditTrailOut,
    BatchIngestRequest,
    BatchIngestResponse,
    DecisionTraceOut,
    ExecutionOut,
    InvoiceListItem,
    InvoiceListResponse,
    InvoiceOut,
    PolicyDecisionOut,
    PromiseOut,
    RunCycleResponse,
)
from app.services import repository
from app.services.executor import ExecutionIntent, build_execution_service
from src.ml.versioning import utc_now

router = APIRouter(
    prefix="/invoices",
    tags=["invoices"],
    dependencies=[Depends(require_api_key)],
)
logger = get_logger(__name__)


@router.post(
    "/batch",
    response_model=BatchIngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("ingest"))],
)
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


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    status: InvoiceStatus | None = Query(default=None),
    escalation_state: EscalationState | None = Query(default=None),
    tier: InterventionTier | None = Query(
        default=None,
        description=(
            "Filter by intervention tier (computed in-memory on the page slice) "
            "Note: because tier is per-page computed, combining this with sort may "
            "return fewer rows per page than requested. Leave empty to see all."
        ),
    ),
    q: str | None = Query(
        default=None, description="Customer name / customer id / invoice id free-text search"
    ),
    sort: str = Query(
        default="expected_value",
        description="One of: expected_value, outstanding, due_date, invoice_id, amount, issue_date",
    ),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> InvoiceListResponse:
    """Paginated, filterable list of open invoices (the MSME work queue page).

    Scoring is dry-run: every row in the returned page is run through the same
    scorer used by the batch report, which yields tier / p_recovery / expected_value
    so the queue can be filtered by tier and sorted by expected_value. No messages
    are sent, no invoice state is advanced, and no decisions are persisted.
    """

    today = utc_now().date()
    actual_status = status if isinstance(status, InvoiceStatus) else None
    actual_escalation = escalation_state if isinstance(escalation_state, EscalationState) else None
    actual_tier = tier if isinstance(tier, InterventionTier) else None
    actual_q = q if isinstance(q, str) and q.strip() else None
    actual_sort = sort if isinstance(sort, str) else "expected_value"
    actual_sort_dir = sort_dir if isinstance(sort_dir, str) else "desc"
    sort_desc = actual_sort_dir.lower() == "desc"
    actual_page = page if isinstance(page, int) else 1
    actual_page_size = page_size if isinstance(page_size, int) else 50

    rows, total = await repository.list_invoices_paginated(
        db,
        status=actual_status,
        escalation_state=actual_escalation,
        customer_search=actual_q,
        only_open=actual_status is None,
        page=actual_page,
        page_size=actual_page_size,
        sort_by=actual_sort,
        sort_desc=sort_desc,
    )

    cases = [await repository.load_case(db, row) for row in rows]

    items: list[InvoiceListItem] = []
    if cases:
        ledger = DecisionLedger()
        scored = run_batch(
            cases,
            config=AgentConfig(policy=policy_config_from_settings()),
            ledger=ledger,
        )
        scored_by_id = {r.invoice_id: r for r in scored}
    else:
        scored_by_id = {}

    for invoice in rows:
        customer = await db.get(Customer, invoice.customer_pk)
        if customer is None:
            continue

        open_promise = await repository.open_promise_for(db, invoice.id)

        score = scored_by_id.get(invoice.invoice_id)
        item_tier = score.tier if score else None
        item_ev = score.score.expected_value if score else None
        p_recovery = score.score.p_recovery if score else None
        rationale = score.score.rationale if score else None

        if actual_tier is not None and item_tier is not None and item_tier != actual_tier:
            continue

        items.append(
            InvoiceListItem(
                invoice_id=invoice.invoice_id,
                customer_id=customer.customer_id,
                customer_name=customer.name,
                amount=invoice.amount,
                amount_paid=invoice.amount_paid,
                outstanding=max(invoice.amount - invoice.amount_paid, 0.0),
                currency=invoice.currency,
                due_date=invoice.due_date,
                days_overdue=max((today - invoice.due_date).days, 0),
                status=invoice.status,
                escalation_state=invoice.escalation_state,
                ladder_index=invoice.ladder_index,
                prior_reminders_sent=invoice.prior_reminders_sent,
                last_contact_at=invoice.last_contact_at,
                tier=item_tier,
                p_recovery=p_recovery,
                expected_value=item_ev,
                promise_status=open_promise.status.value if open_promise else None,
                promise_due_date=open_promise.promised_date if open_promise else None,
                rationale=rationale,
            )
        )

    if sort == "expected_value" or sort == "ev":
        reverse = sort_desc
        items.sort(
            key=lambda it: it.expected_value if it.expected_value is not None else -1,
            reverse=reverse,
        )
    elif sort == "tier":
        tier_rank = {
            InterventionTier.ESCALATE: 0,
            InterventionTier.REMIND: 1,
            InterventionTier.WAIT: 2,
            None: 3,
        }
        reverse = sort_desc
        items.sort(key=lambda it: tier_rank.get(it.tier, 3), reverse=reverse)
    elif sort == "days_overdue":
        reverse = sort_desc
        items.sort(key=lambda it: it.days_overdue, reverse=reverse)

    # Total counts the pre-tier-filter query, not the page slice: tier is
    # computed in memory per row, so the page may hold fewer rows than asked.
    visible_total = total

    total_pages = max(1, math.ceil(visible_total / page_size))

    return InvoiceListResponse(
        items=items,
        total=visible_total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


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
