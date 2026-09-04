"""Drift flags: the nightly verdicts, newest first.

A flag means "this customer's recent behavior no longer resembles normal
payers -- a human should look". It changes no invoice state, freezes
nothing, and sends nothing; the review itself happens wherever the team
works, starting from the drivers each flag carries.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import require_api_key
from app.core.tenancy import TenantContext, require_tenant
from app.db.session import get_db
from app.models import CustomerDriftFlag
from app.schemas.drift import DriftDriverOut, DriftFlagListOut, DriftFlagOut
from app.services import repository

router = APIRouter(prefix="/drift", tags=["drift"], dependencies=[Depends(require_api_key)])
logger = get_logger(__name__)


def _to_out(customer_id: str, customer_name: str, flag: CustomerDriftFlag) -> DriftFlagOut:
    drivers = [
        DriftDriverOut(
            feature=str(item.get("feature", "")),
            value=float(item.get("value", 0.0)),
            deviation=float(item.get("deviation", 0.0)),
        )
        for item in (flag.details.get("top_drivers") or [])
        if isinstance(item, dict)
    ]
    return DriftFlagOut(
        customer_id=customer_id,
        customer_name=customer_name,
        anomaly_score=flag.anomaly_score,
        threshold=flag.threshold,
        flagged=flag.flagged,
        model_version=flag.model_version,
        window_days=flag.window_days,
        top_drivers=drivers,
        created_at=flag.created_at,
    )


@router.get("/flags", response_model=DriftFlagListOut)
async def list_flags(
    limit: int = Query(default=50, ge=1, le=200),
    only_flagged: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> DriftFlagListOut:
    """Recent drift verdicts with their customers, newest first, one tenant."""

    rows = await repository.recent_drift_flags(
        db, tenant.business_id, limit=limit, only_flagged=only_flagged
    )
    return DriftFlagListOut(
        count=len(rows),
        items=[_to_out(customer.customer_id, customer.name, flag) for flag, customer in rows],
    )


@router.get("/flags/{customer_id}", response_model=DriftFlagOut)
async def latest_flag(
    customer_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> DriftFlagOut:
    """The most recent drift verdict for one customer in one business."""

    customer = await repository.get_customer(db, customer_id, tenant.business_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Customer {customer_id} not found")
    flag = await repository.latest_drift_flag_for(db, customer.id, tenant.business_id)
    if flag is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No drift verdict recorded for {customer_id}"
        )
    return _to_out(customer.customer_id, customer.name, flag)
