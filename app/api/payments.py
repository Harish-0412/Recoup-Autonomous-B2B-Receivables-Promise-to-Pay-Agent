"""Bank payment / UTR endpoints.

Three routes cover the full operator workflow for recording a bank transfer:

    POST /payments/bank
        Operator pastes a UTR from the bank statement. The UTR matcher runs
        and returns a confidence verdict. The allocation is written as
        unmatched (invoice_pk=NULL) regardless of confidence; matching is
        advisory, confirming is the operator's responsibility.

    GET  /payments/unmatched
        Review queue of bank UTR allocations that have no invoice yet.
        Oldest-first so the oldest unresolved entry is at the top.

    POST /payments/{allocation_id}/allocate
        Human confirms that a UTR belongs to a specific invoice. Validates
        tenancy, checks the invoice is open, links the allocation, and
        recomputes amount_paid from the ledger.

Safety invariants enforced here:
* Never auto-match when the matcher returns ``ambiguous`` or ``no_match``.
* Never link an allocation to an invoice in a different tenant.
* Never link an allocation that is already linked to a different invoice.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import require_api_key
from app.core.tenancy import TenantContext, require_tenant
from app.core.utr_matcher import InvoiceMatchCandidate, match_bank_payment
from app.db.session import get_db
from app.models.enums import AllocationSource, InvoiceStatus
from app.schemas.payments import (
    AllocateIn,
    AllocateOut,
    BankPaymentIn,
    BankPaymentOut,
    UnmatchedPaymentOut,
    UnmatchedPaymentsResponse,
)
from app.services import repository
from src.ml.versioning import utc_now

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(require_api_key)],
)
logger = get_logger(__name__)


@router.post(
    "/bank",
    response_model=BankPaymentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a bank payment (NEFT/RTGS/UPI) by UTR",
)
async def post_bank_payment(
    payload: BankPaymentIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> BankPaymentOut:
    """Operator submits a UTR from the bank statement.

    1. Runs the UTR matcher against all open invoices for this tenant.
    2. Writes the allocation as unmatched (invoice_pk=NULL).
    3. Returns the match confidence and, if probable, the suggested invoice.
    4. The operator must call POST /payments/{allocation_id}/allocate to confirm.

    Idempotent on UTR: if the same UTR has already been recorded for this tenant,
    returns 409 Conflict with the existing allocation.
    """
    business_id = tenant.business_id

    # Fetch all open invoices and existing UTRs for this tenant
    open_invoice_rows = await repository.list_open_invoice_candidates(db, business_id)
    existing_utrs = await repository.utrs_for_tenant(db, business_id)

    # Also fetch the most recent pending promise per invoice for date-window matching
    promise_dates: dict[int, date] = {}
    for inv_row in open_invoice_rows:
        promise = await repository.open_promise_for(db, inv_row.id, business_id)
        if promise is not None:
            promise_dates[inv_row.id] = promise.promised_date

    candidates = [
        InvoiceMatchCandidate(
            invoice_pk=inv.id,
            invoice_id=inv.invoice_id,
            customer_pk=inv.customer_pk,
            customer_id=(
                # Load customer_id lazily via DB if needed
                inv.invoice_id  # placeholder replaced below
            ),
            amount=inv.amount,
            outstanding=max(inv.amount - inv.amount_paid, 0.0),
            currency=inv.currency,
            due_date=inv.due_date,
            promise_date=promise_dates.get(inv.id),
        )
        for inv in open_invoice_rows
    ]

    # Enrich candidates with customer_id (cheap: customer_pk is already loaded)
    from app.models.tables import Customer

    enriched: list[InvoiceMatchCandidate] = []
    for inv, cand in zip(open_invoice_rows, candidates, strict=False):
        customer = await db.get(Customer, inv.customer_pk)
        cid = customer.customer_id if customer else inv.invoice_id
        enriched.append(
            InvoiceMatchCandidate(
                invoice_pk=cand.invoice_pk,
                invoice_id=cand.invoice_id,
                customer_pk=cand.customer_pk,
                customer_id=cid,
                amount=cand.amount,
                outstanding=cand.outstanding,
                currency=cand.currency,
                due_date=cand.due_date,
                promise_date=cand.promise_date,
            )
        )

    match = match_bank_payment(
        utr=payload.utr,
        amount=payload.amount,
        paid_on=payload.paid_on,
        payer_account=payload.payer_account,
        suggested_invoice_id=payload.suggested_invoice_id,
        open_invoices=enriched,
        existing_utrs=existing_utrs,
    )

    if match.confidence == "duplicate":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=match.reason,
        )

    # Record the allocation as UNMATCHED (invoice_pk=NULL).
    # Confirmed match happens via the separate /allocate endpoint.
    allocation = await repository.create_allocation(
        db,
        business_id=business_id,
        invoice_pk=None,
        source=AllocationSource.BANK_UTR,
        provider_ref=payload.utr,
        amount=payload.amount,
        currency=payload.currency,
        received_at=utc_now().replace(
            year=payload.paid_on.year,
            month=payload.paid_on.month,
            day=payload.paid_on.day,
        ),
        recorded_by="operator",
        payer_account=payload.payer_account,
        notes=payload.notes,
    )

    if allocation is None:
        # Race: written between the UTR check and the insert
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"UTR {payload.utr!r} was just recorded by a concurrent request.",
        )

    await db.commit()

    logger.info(
        "Bank payment recorded",
        utr=payload.utr,
        amount=payload.amount,
        match_confidence=match.confidence,
        business_id=business_id,
    )

    return BankPaymentOut(
        allocation_id=allocation.id,
        utr=payload.utr,
        amount=payload.amount,
        currency=payload.currency,
        paid_on=payload.paid_on,
        payer_account=payload.payer_account,
        notes=payload.notes,
        recorded_by="operator",
        created_at=allocation.created_at,
        match_confidence=match.confidence,
        match_reason=match.reason,
        suggested_invoice_id=match.invoice_id,
    )


@router.get(
    "/unmatched",
    response_model=UnmatchedPaymentsResponse,
    summary="List unmatched bank UTR payments awaiting human confirmation",
)
async def list_unmatched_payments(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> UnmatchedPaymentsResponse:
    """Review queue of bank UTR allocations without an invoice match.

    Oldest-first so the most overdue entries surface first. Each item carries
    the UTR, amount, and payer hint the operator submitted. Use the allocation_id
    to confirm a match via POST /payments/{allocation_id}/allocate.
    """
    rows = await repository.unmatched_bank_payments(db, tenant.business_id, limit=limit)
    items = [
        UnmatchedPaymentOut(
            allocation_id=row.id,
            utr=row.provider_ref,
            amount=row.amount,
            currency=row.currency,
            paid_on=row.received_at.date(),
            payer_account=row.payer_account,
            notes=row.notes,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return UnmatchedPaymentsResponse(items=items, total=len(items))


@router.post(
    "/{allocation_id}/allocate",
    response_model=AllocateOut,
    summary="Confirm which invoice a bank UTR payment belongs to",
)
async def confirm_payment_allocation(
    allocation_id: int,
    payload: AllocateIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> AllocateOut:
    """Human confirms the invoice for an unmatched bank UTR.

    Validates:
    * The allocation belongs to this tenant.
    * The allocation has not already been linked to a different invoice.
    * The invoice is open (OPEN, IN_PROGRESS, or PROMISED).
    * The invoice belongs to this tenant.

    After confirmation, ``invoices.amount_paid`` is recomputed from the
    allocation ledger (SUM of all allocations for that invoice).
    """
    business_id = tenant.business_id

    allocation = await repository.get_allocation(db, allocation_id, business_id)
    if allocation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Allocation {allocation_id} not found")

    invoice = await repository.get_invoice(db, payload.invoice_id, business_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Invoice {payload.invoice_id!r} not found")

    _open_statuses = {InvoiceStatus.OPEN, InvoiceStatus.IN_PROGRESS, InvoiceStatus.PROMISED}
    if invoice.status not in _open_statuses:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Invoice {payload.invoice_id!r} is {invoice.status.value}; only open invoices can receive allocations.",
        )

    try:
        new_amount_paid = await repository.confirm_allocation(db, allocation, invoice, business_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    await db.commit()

    logger.info(
        "Bank allocation confirmed",
        allocation_id=allocation_id,
        invoice_id=payload.invoice_id,
        new_amount_paid=new_amount_paid,
        business_id=business_id,
    )

    return AllocateOut(
        allocation_id=allocation_id,
        invoice_id=invoice.invoice_id,
        new_amount_paid=new_amount_paid,
        new_outstanding=max(invoice.amount - new_amount_paid, 0.0),
        invoice_status=invoice.status.value,
        currency=invoice.currency,
    )
