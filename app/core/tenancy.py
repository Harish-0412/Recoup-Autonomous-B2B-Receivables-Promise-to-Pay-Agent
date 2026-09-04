"""Wave 1 tenant context. One Postgres, many businesses.

No /tenants routes in the agent loop. Every operator request resolves a
TenantContext.business_id from the bearer credential presented, and every
repository call takes that id and filters by it. RLS (SET app.business_id)
is defense-in-depth later; repository scoping is the enforcement.

v1 resolution order for a bearer token:
  1. businesses table lookup by api_key / task_api_key (per-tenant keys)
  2. Settings.API_KEY / Settings.TASK_API_KEY fallback -> Settings.BUSINESS_ID
     (single-operator deploy; backfill default 'default')

Cron TASK_API_KEY is scoped to one business per key, never "all tenants".
Webhooks carry no bearer; their tenant comes from Razorpay notes.business_id.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import require_api_key, require_task_key
from app.db.session import get_db
from app.models.tables import DEFAULT_BUSINESS_ID

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TenantContext:
    business_id: str


def _bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token


async def _business_for_token(db: AsyncSession, token: str) -> str | None:
    """Per-tenant key lookup. Returns business_id or None."""
    if not token:
        return None
    # Avoid importing Business at module scope cycles: local import is cheap.
    from app.models.tables import Business

    result = await db.execute(
        select(Business.business_id).where(
            (Business.api_key == token) | (Business.task_api_key == token)
        )
    )
    row = result.scalar_one_or_none()
    return row


async def resolve_tenant_from_token(db: AsyncSession, token: str) -> str:
    """Resolve business_id for any bearer token, falling back to settings."""
    settings = get_settings()
    tenant = await _business_for_token(db, token)
    if tenant:
        return tenant
    # Constant-time compare against configured single-operator keys.
    candidates = [k for k in (settings.API_KEY, settings.TASK_API_KEY) if k]
    if token and any(secrets.compare_digest(token, k) for k in candidates):
        return settings.BUSINESS_ID or DEFAULT_BUSINESS_ID
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_tenant(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    _authed: None = Depends(require_api_key),
) -> TenantContext:
    """Operator tenant. Runs after require_api_key so unauthenticated is 401/503 first."""
    token = _bearer_token(authorization)
    business_id = await resolve_tenant_from_token(db, token)
    return TenantContext(business_id=business_id)


async def require_task_tenant(
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    _authed: None = Depends(require_task_key),
) -> TenantContext:
    """Cron tenant. One key maps to exactly one business, never all tenants."""
    settings = get_settings()
    token = _bearer_token(authorization)
    tenant = await _business_for_token(db, token)
    if tenant:
        return TenantContext(business_id=tenant)
    if settings.TASK_API_KEY and secrets.compare_digest(token, settings.TASK_API_KEY):
        return TenantContext(business_id=settings.BUSINESS_ID or DEFAULT_BUSINESS_ID)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated.",
        headers={"WWW-Authenticate": "Bearer"},
    )
