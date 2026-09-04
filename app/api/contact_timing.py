"""Send-time recommendations: when a reminder is most likely to be answered.

Read-only. The bandit's posterior is loaded from the versioned artifact store;
this endpoint never mutates it. Learning happens on the reply path, where a
genuine customer response is the reward -- see
``app.services.contact_timing.record_reply_engagement``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import require_api_key
from app.core.tenancy import TenantContext, require_tenant
from app.db.session import get_db
from app.schemas.contact_timing import NextTimeOut
from app.services import contact_timing, repository

router = APIRouter(prefix="/schedule", tags=["schedule"], dependencies=[Depends(require_api_key)])
logger = get_logger(__name__)


@router.get("/next_time", response_model=NextTimeOut)
async def next_time(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> NextTimeOut:
    """The optimal datetime for the next reminder to this customer.

    404 on an unknown customer: guessing a slot for a customer that does not
    exist would be a stranger's reminder schedule, not a recommendation.
    """

    customer = await repository.get_customer(db, customer_id, tenant.business_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Customer {customer_id} not found")

    segment = contact_timing.segment_for_snapshot(customer)
    loaded = contact_timing.load_bandit()
    if loaded is None:
        suggestion = contact_timing.fallback_suggestion(customer_id, segment=segment)
    else:
        bandit, _ = loaded
        picked = bandit.suggest(segment)
        suggestion = contact_timing.TimingSuggestion(
            customer_id=customer_id,
            segment=segment,
            arm=picked.arm,
            scheduled_for=contact_timing.next_occurrence(picked.arm),
            expected_response_rate=round(picked.expected_response_rate, 6),
            observations=picked.observations,
            backed_off_to_global=picked.backed_off_to_global,
        )

    logger.info(
        "Send-time suggested",
        customer_id=customer_id,
        arm=suggestion.arm,
        fallback=suggestion.fallback_used,
    )
    return NextTimeOut(**suggestion.model_dump())
