"""ERP / accounting integration endpoints.

Connect, sync, and status endpoints for each supported provider:
    Zoho Books   — OAuth2 connect + hourly sync
    QuickBooks   — Intuit OAuth2 connect + hourly sync
    Razorpay     — Same API keys; sync unpaid invoices
    Tally        — File upload (CSV or XML); no cloud REST API

All routes require operator authentication (require_api_key). OAuth callback
handling accepts a short-lived authorization code from the provider's redirect
and exchanges it for tokens, which are stored in integration_credentials.

Sync routes are advisory-locked per business so a slow sync does not pile up.
The lock_key is the same mechanism used by the existing batch runner.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import require_api_key
from app.core.tenancy import TenantContext, require_tenant
from app.db.session import get_db
from app.models.enums import IntegrationProvider
from app.schemas.integrations import (
    IntegrationStatusOut,
    OAuthConnectIn,
    OAuthConnectOut,
    SyncResponse,
    TallyImportResponse,
)
from app.services import repository
from src.ml.versioning import utc_now

router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_api_key)],
)
logger = get_logger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Zoho Books
# ---------------------------------------------------------------------------


@router.post(
    "/zoho/connect",
    response_model=OAuthConnectOut,
    status_code=status.HTTP_200_OK,
    summary="Exchange Zoho OAuth code for tokens",
)
async def zoho_connect(
    payload: OAuthConnectIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> OAuthConnectOut:
    """Exchange a Zoho OAuth2 authorization code for tokens.

    The frontend should redirect the user to:
    https://accounts.zoho.in/oauth/v2/auth?client_id={ZOHO_CLIENT_ID}&...
    then POST the returned code here.
    """
    from datetime import timedelta

    import httpx

    if not settings.ZOHO_CLIENT_ID or not settings.ZOHO_CLIENT_SECRET:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "Zoho OAuth credentials not configured"
        )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://accounts.zoho.in/oauth/v2/token",
            data={
                "code": payload.code,
                "client_id": settings.ZOHO_CLIENT_ID,
                "client_secret": settings.ZOHO_CLIENT_SECRET,
                "redirect_uri": payload.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Zoho token exchange failed: {resp.text[:200]}",
            )
        token_data = resp.json()

    expires_at = utc_now() + timedelta(seconds=int(token_data.get("expires_in", 3600)))
    org_id = payload.extras.get("org_id", "")

    await repository.upsert_integration_credential(
        db,
        tenant.business_id,
        IntegrationProvider.ZOHO_BOOKS,
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token", ""),
        token_expires_at=expires_at,
        scope=token_data.get("scope", ""),
        extra={"org_id": org_id},
    )
    await db.commit()

    return OAuthConnectOut(
        provider="zoho_books",
        connected=True,
        scope=token_data.get("scope", ""),
        expires_at=expires_at,
        message="Zoho Books connected. Run /integrations/zoho/sync to pull invoices.",
    )


@router.post(
    "/zoho/sync",
    response_model=SyncResponse,
    summary="Sync overdue Zoho Books invoices",
)
async def zoho_sync(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> SyncResponse:
    """Pull overdue invoices from Zoho Books and upsert them into Recoup.

    Idempotent: running twice does not duplicate customers or invoices.
    Credit notes from Zoho become negative allocations.
    """
    from app.services.integrations import zoho

    credential = await repository.get_integration_credential(
        db, tenant.business_id, IntegrationProvider.ZOHO_BOOKS
    )
    if credential is None or not credential.refresh_token:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Zoho Books not connected. Call POST /integrations/zoho/connect first.",
        )

    org_id = (credential.extra or {}).get("org_id", "")
    result = await zoho.sync(
        db,
        tenant.business_id,
        org_id=org_id,
        access_token=credential.access_token,
        refresh_token=credential.refresh_token,
        client_id=settings.ZOHO_CLIENT_ID,
        client_secret=settings.ZOHO_CLIENT_SECRET,
        lookback_days=settings.ERP_SYNC_LOOKBACK_DAYS,
    )

    await repository.update_sync_stats(
        db,
        credential,
        invoices_synced=result.invoices_created + result.invoices_updated,
        errors=len(result.errors),
    )
    await db.commit()

    return SyncResponse(
        provider="zoho_books",
        customers_created=result.customers_created,
        customers_updated=result.customers_updated,
        invoices_created=result.invoices_created,
        invoices_updated=result.invoices_updated,
        invoices_skipped=result.invoices_skipped,
        credit_notes_applied=result.credit_notes_applied,
        errors=result.errors,
        synced_at=utc_now(),
    )


@router.get(
    "/zoho/status",
    response_model=IntegrationStatusOut,
    summary="Zoho Books integration status",
)
async def zoho_status(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> IntegrationStatusOut:
    credential = await repository.get_integration_credential(
        db, tenant.business_id, IntegrationProvider.ZOHO_BOOKS
    )
    connected = credential is not None and bool(credential.refresh_token)
    return IntegrationStatusOut(
        provider="zoho_books",
        connected=connected,
        last_sync_at=credential.last_sync_at if credential else None,
        last_sync_invoices=credential.last_sync_invoices if credential else 0,
        last_sync_errors=credential.last_sync_errors if credential else 0,
        token_expires_at=credential.token_expires_at if credential else None,
    )


# ---------------------------------------------------------------------------
# QuickBooks Online
# ---------------------------------------------------------------------------


@router.post(
    "/quickbooks/connect",
    response_model=OAuthConnectOut,
    summary="Exchange QuickBooks OAuth code for tokens",
)
async def quickbooks_connect(
    payload: OAuthConnectIn,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> OAuthConnectOut:
    """Exchange a QuickBooks Online Intuit OAuth2 authorization code for tokens."""
    import base64
    from datetime import timedelta

    import httpx

    if not settings.QBO_CLIENT_ID or not settings.QBO_CLIENT_SECRET:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "QuickBooks OAuth credentials not configured"
        )

    credentials = base64.b64encode(
        f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "authorization_code",
                "code": payload.code,
                "redirect_uri": payload.redirect_uri,
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"QuickBooks token exchange failed: {resp.text[:200]}",
            )
        token_data = resp.json()

    expires_at = utc_now() + timedelta(seconds=int(token_data.get("expires_in", 3600)))
    realm_id = payload.extras.get("realm_id", "")

    await repository.upsert_integration_credential(
        db,
        tenant.business_id,
        IntegrationProvider.QUICKBOOKS,
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token", ""),
        token_expires_at=expires_at,
        scope=token_data.get("scope", ""),
        extra={"realm_id": realm_id, "environment": settings.QBO_ENVIRONMENT},
    )
    await db.commit()

    return OAuthConnectOut(
        provider="quickbooks",
        connected=True,
        scope=token_data.get("scope", ""),
        expires_at=expires_at,
        message="QuickBooks Online connected. Run /integrations/quickbooks/sync to pull invoices.",
    )


@router.post(
    "/quickbooks/sync",
    response_model=SyncResponse,
    summary="Sync overdue QuickBooks Online invoices",
)
async def quickbooks_sync(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> SyncResponse:
    """Pull invoices with balance > 0 from QuickBooks Online and upsert them."""
    from app.services.integrations import quickbooks

    credential = await repository.get_integration_credential(
        db, tenant.business_id, IntegrationProvider.QUICKBOOKS
    )
    if credential is None or not credential.refresh_token:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "QuickBooks not connected. Call POST /integrations/quickbooks/connect first.",
        )

    realm_id = (credential.extra or {}).get("realm_id", "")
    environment = (credential.extra or {}).get("environment", settings.QBO_ENVIRONMENT)

    result = await quickbooks.sync(
        db,
        tenant.business_id,
        realm_id=realm_id,
        access_token=credential.access_token,
        refresh_token=credential.refresh_token,
        client_id=settings.QBO_CLIENT_ID,
        client_secret=settings.QBO_CLIENT_SECRET,
        environment=environment,
        lookback_days=settings.ERP_SYNC_LOOKBACK_DAYS,
    )

    await repository.update_sync_stats(
        db,
        credential,
        invoices_synced=result.invoices_created + result.invoices_updated,
        errors=len(result.errors),
    )
    await db.commit()

    return SyncResponse(
        provider="quickbooks",
        customers_created=result.customers_created,
        customers_updated=result.customers_updated,
        invoices_created=result.invoices_created,
        invoices_updated=result.invoices_updated,
        invoices_skipped=result.invoices_skipped,
        credit_notes_applied=result.credit_notes_applied,
        errors=result.errors,
        synced_at=utc_now(),
    )


@router.get(
    "/quickbooks/status",
    response_model=IntegrationStatusOut,
    summary="QuickBooks integration status",
)
async def quickbooks_status(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> IntegrationStatusOut:
    credential = await repository.get_integration_credential(
        db, tenant.business_id, IntegrationProvider.QUICKBOOKS
    )
    connected = credential is not None and bool(credential.refresh_token)
    return IntegrationStatusOut(
        provider="quickbooks",
        connected=connected,
        last_sync_at=credential.last_sync_at if credential else None,
        last_sync_invoices=credential.last_sync_invoices if credential else 0,
        last_sync_errors=credential.last_sync_errors if credential else 0,
        token_expires_at=credential.token_expires_at if credential else None,
    )


# ---------------------------------------------------------------------------
# Razorpay Invoices
# ---------------------------------------------------------------------------


@router.post(
    "/razorpay/sync",
    response_model=SyncResponse,
    summary="Sync unpaid Razorpay invoices",
)
async def razorpay_sync(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> SyncResponse:
    """Pull unpaid Razorpay Invoices (not payment links) and upsert them.

    Uses the same RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET from settings.
    Paid invoices are skipped — they are already handled by the webhook path.
    """
    from app.services.integrations import razorpay_invoices

    result = await razorpay_invoices.sync(
        db,
        tenant.business_id,
        key_id=settings.RAZORPAY_KEY_ID,
        key_secret=settings.RAZORPAY_KEY_SECRET,
        lookback_days=settings.ERP_SYNC_LOOKBACK_DAYS,
    )

    # Update or create a credential row for tracking (no OAuth needed here)
    credential = await repository.upsert_integration_credential(
        db,
        tenant.business_id,
        IntegrationProvider.RAZORPAY_INVOICES,
        access_token="",
        refresh_token="",
        scope="",
    )
    await repository.update_sync_stats(
        db,
        credential,
        invoices_synced=result.invoices_created + result.invoices_updated,
        errors=len(result.errors),
    )
    await db.commit()

    return SyncResponse(
        provider="razorpay_invoices",
        customers_created=result.customers_created,
        customers_updated=result.customers_updated,
        invoices_created=result.invoices_created,
        invoices_updated=result.invoices_updated,
        invoices_skipped=result.invoices_skipped,
        errors=result.errors,
        synced_at=utc_now(),
    )


@router.get(
    "/razorpay/status",
    response_model=IntegrationStatusOut,
    summary="Razorpay Invoices integration status",
)
async def razorpay_status(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> IntegrationStatusOut:
    credential = await repository.get_integration_credential(
        db, tenant.business_id, IntegrationProvider.RAZORPAY_INVOICES
    )
    return IntegrationStatusOut(
        provider="razorpay_invoices",
        connected=True,  # Always connected — uses global keys
        last_sync_at=credential.last_sync_at if credential else None,
        last_sync_invoices=credential.last_sync_invoices if credential else 0,
        last_sync_errors=credential.last_sync_errors if credential else 0,
        token_expires_at=None,  # No OAuth token
    )


# ---------------------------------------------------------------------------
# Tally (file upload)
# ---------------------------------------------------------------------------


@router.post(
    "/tally/import",
    response_model=TallyImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import Tally CSV or XML export file",
)
async def tally_import(
    file: UploadFile = File(..., description="Tally CSV or XML export file"),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> TallyImportResponse:
    """Accept a Tally export file (CSV or XML) and upsert customers/invoices.

    This is the correct integration approach for Tally: operator exports from
    Tally and uploads the file here. No REST client, no fake Tally API.

    File format is auto-detected from the Content-Type or filename extension.
    CSV columns: voucher_number, ledger_name, date, amount (plus optional fields).
    XML: TallyPrime VOUCHER elements.
    """
    from app.services.integrations import tally

    filename = file.filename or "tally_export"
    raw_bytes = await file.read()
    content = raw_bytes.decode("utf-8", errors="replace")

    # Detect format
    ct = (file.content_type or "").lower()
    is_xml = "xml" in ct or filename.lower().endswith(".xml")
    fmt = "xml" if is_xml else "csv"

    if is_xml:
        result = await tally.import_xml(
            db, tenant.business_id, file_content=content, filename=filename
        )
    else:
        result = await tally.import_csv(
            db, tenant.business_id, file_content=content, filename=filename
        )

    # Update Tally credential row for tracking last import
    credential = await repository.upsert_integration_credential(
        db,
        tenant.business_id,
        IntegrationProvider.TALLY,
        extra={
            "last_import_filename": filename,
            "last_import_rows": result.invoices_created + result.invoices_updated,
        },
    )
    await repository.update_sync_stats(
        db,
        credential,
        invoices_synced=result.invoices_created + result.invoices_updated,
        errors=len(result.errors),
    )
    await db.commit()

    return TallyImportResponse(
        filename=filename,
        format=fmt,
        customers_created=result.customers_created,
        customers_updated=result.customers_updated,
        invoices_created=result.invoices_created,
        invoices_updated=result.invoices_updated,
        invoices_skipped=result.invoices_skipped,
        errors=result.errors,
        imported_at=utc_now(),
    )


@router.get(
    "/tally/status",
    response_model=IntegrationStatusOut,
    summary="Tally integration status (last file import)",
)
async def tally_status(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
) -> IntegrationStatusOut:
    credential = await repository.get_integration_credential(
        db, tenant.business_id, IntegrationProvider.TALLY
    )
    return IntegrationStatusOut(
        provider="tally",
        connected=credential is not None,
        last_sync_at=credential.last_sync_at if credential else None,
        last_sync_invoices=credential.last_sync_invoices if credential else 0,
        last_sync_errors=credential.last_sync_errors if credential else 0,
        token_expires_at=None,
    )
