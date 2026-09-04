"""The endpoints an external scheduler calls. This is what makes it autonomous.

**Why the schedule lives outside the process.** An in-process scheduler
(APScheduler and friends) is one line to add and double-fires the moment a
second replica starts -- and the second replica is exactly what you add when
the service gets busy, so the failure arrives precisely when it is worst. The
schedule belongs to whatever already runs reliably on a timer: Render Cron, a
GitHub Actions schedule, a Kubernetes CronJob. The app's job is to be safe when
that thing misbehaves.

**And it does misbehave.** Cron retries, two schedulers get configured by
mistake, a run takes longer than its interval. So every run takes a Postgres
advisory lock and a second concurrent trigger is a no-op, not a queued rerun --
waiting for the lock would simply do the double-send later.

    POST /api/v1/tasks/run-batch    sweep promises, then run decision cycles
    GET  /api/v1/tasks/status       what the agent believes about itself

Both are authenticated. They send real email, and an open trigger endpoint is
an open way to mail an entire customer book.
"""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import DecisionLedger
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import require_task_key
from app.core.tenancy import TenantContext, require_task_tenant
from app.db.session import get_db
from app.models.tables import BatchRunRecord
from app.services import repository
from app.services.batch_runner import (
    BATCH_LOCK,
    RunInvoiceDecision,
    RunSummary,
    run_batch_cycle,
    sweep_promises,
)
from app.services.locks import advisory_lock
from src.ml.versioning import utc_now

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_task_key)],
)
logger = get_logger(__name__)


def _record_to_summary(record: BatchRunRecord) -> RunSummary:
    decisions: list[RunInvoiceDecision] = []
    for d in record.invoice_decisions or []:
        # A stored decision that no longer validates against the current
        # schema is skipped, not fatal: history must stay readable.
        with suppress(Exception):
            decisions.append(RunInvoiceDecision(**d))
    return RunSummary(
        run_id=record.run_id,
        started_at=record.started_at,
        finished_at=record.finished_at,
        ran=record.ran,
        skipped_reason=record.skipped_reason,
        promises_checked=record.promises_checked,
        promises_broken=record.promises_broken,
        promises_kept=record.promises_kept,
        invoices_considered=record.invoices_considered,
        scored=record.scored,
        acted=record.acted,
        blocked_by_policy=record.blocked_by_policy,
        left_alone=record.left_alone,
        delivery_failed=record.delivery_failed,
        handed_off=record.handed_off,
        sending_halted=record.sending_halted,
        errors=record.errors or [],
        invoice_decisions=decisions,
    )


@router.post("/run-batch", response_model=RunSummary)
async def run_batch(
    limit: int | None = Query(default=None, gt=0, description="Override the batch cap"),
    dry_run: bool | None = Query(default=None, description="Override dry run execution mode"),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_task_tenant),
) -> RunSummary:
    """Run one autonomous cycle over the open book.

    Returns a summary whether or not it did anything. A run that was skipped
    because another was already going reports ``ran=false`` with a reason
    rather than a bare 200, so a cron log shows the difference between "nothing
    to do" and "someone else is doing it".
    """

    settings = get_settings()
    summary = RunSummary(started_at=utc_now().isoformat())
    ledger = DecisionLedger()

    async with advisory_lock(db, BATCH_LOCK) as acquired:
        if not acquired:
            summary.ran = False
            summary.skipped_reason = (
                "Another batch run is already in progress; this trigger was a no-op."
            )
            summary.finished_at = utc_now().isoformat()
            logger.info("Batch run skipped: lock held")
            return summary

        # Promises first: a promise that lapsed this morning should make its
        # invoice chaseable in *this* run, not the next one. Scoped to the
        # cron key's single business -- never "all tenants".
        checked, broken, kept = await sweep_promises(db, tenant.business_id, ledger=ledger)
        summary.promises_checked = checked
        summary.promises_broken = broken
        summary.promises_kept = kept

        await run_batch_cycle(
            db,
            tenant.business_id,
            limit=limit or settings.BATCH_MAX_INVOICES,
            sending_enabled=settings.SENDING_ENABLED,
            ledger=ledger,
            summary=summary,
            dry_run=dry_run,
        )

        await repository.persist_ledger(db, ledger, tenant.business_id)

        # Persist batch run record and per-invoice decision breakdown
        summary.finished_at = utc_now().isoformat()
        record = BatchRunRecord(
            run_id=summary.run_id,
            started_at=summary.started_at,
            finished_at=summary.finished_at,
            ran=summary.ran,
            skipped_reason=summary.skipped_reason,
            promises_checked=summary.promises_checked,
            promises_broken=summary.promises_broken,
            promises_kept=summary.promises_kept,
            invoices_considered=summary.invoices_considered,
            scored=summary.scored,
            acted=summary.acted,
            blocked_by_policy=summary.blocked_by_policy,
            left_alone=summary.left_alone,
            delivery_failed=summary.delivery_failed,
            handed_off=summary.handed_off,
            sending_halted=summary.sending_halted,
            errors=summary.errors,
            invoice_decisions=[d.model_dump() for d in summary.invoice_decisions],
        )
        await repository.save_batch_run(db, record, tenant.business_id)
        await db.commit()

    if not summary.finished_at:
        summary.finished_at = utc_now().isoformat()
    logger.info(
        "Batch run complete",
        run_id=summary.run_id,
        promises_checked=summary.promises_checked,
        promises_broken=summary.promises_broken,
        invoices_considered=summary.invoices_considered,
        scored=summary.scored,
        acted=summary.acted,
        blocked=summary.blocked_by_policy,
        left_alone=summary.left_alone,
        failed=summary.delivery_failed,
        handed_off=summary.handed_off,
        sending_halted=summary.sending_halted,
        errors=len(summary.errors),
        decisions=len(summary.invoice_decisions),
    )
    return summary


@router.get("/runs/latest", response_model=RunSummary | None)
async def get_latest_run(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_task_tenant),
) -> RunSummary | None:
    """Retrieve the latest completed autonomous batch run with its live decision items."""
    record = await repository.get_latest_batch_run(db, tenant.business_id)
    if record is None:
        return None
    return _record_to_summary(record)


@router.get("/runs", response_model=list[RunSummary])
async def list_past_runs(
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_task_tenant),
) -> list[RunSummary]:
    """List historical autonomous runs, most recent first."""
    records = await repository.list_batch_runs(db, tenant.business_id, limit=limit)
    return [_record_to_summary(r) for r in records]


@router.get("/status")
async def task_status(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_task_tenant),
) -> dict:
    """What the agent currently believes about itself.

    Exists so the kill switch can be confirmed from outside the process. "Is
    sending actually off?" should be answerable without reading logs or
    trusting that a config change took effect.
    """

    settings = get_settings()
    open_invoices = await repository.list_open_invoices(
        db, tenant.business_id, limit=settings.BATCH_MAX_INVOICES
    )
    pending = await repository.pending_promises(db, tenant.business_id)
    for_review = await repository.replies_needing_review(db, tenant.business_id, limit=500)
    drift_flagged = await repository.count_recently_flagged_customers(
        db, tenant.business_id, days=7
    )

    return {
        "business_id": tenant.business_id,
        "sending_enabled": settings.SENDING_ENABLED,
        "dry_run": settings.DRY_RUN,
        "batch_max_invoices": settings.BATCH_MAX_INVOICES,
        "expected_interval_seconds": settings.SCHEDULER_INTERVAL_SECONDS,
        "open_invoices": len(open_invoices),
        "pending_promises": len(pending),
        "replies_awaiting_review": len(for_review),
        "customers_flagged_drift_7d": drift_flagged,
    }


# ---------------------------------------------------------------------------
# Wave 2: ERP sync cron task
# ---------------------------------------------------------------------------

#: Advisory lock key for the ERP sync job. Different from BATCH_LOCK so a
#: slow ERP sync does not block the main collection batch.
ERP_SYNC_LOCK = 99_002


@router.post(
    "/sync-erp",
    summary="Trigger an ERP sync for all connected integrations",
)
async def sync_erp(
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(require_task_tenant),
) -> dict:
    """Iterate all connected ERP integrations for this tenant and sync.

    Safe to call from the external cron alongside run-batch. Advisory-locked
    per business so a concurrent invocation is a no-op rather than a queued
    rerun. Returns a summary keyed by provider.

    Providers synced (if credentials exist):
    * zoho_books   — overdue invoices via Zoho Books API
    * quickbooks   — open invoices via Intuit QBO V3 Query API
    * razorpay_invoices — unpaid invoices via Razorpay Invoices API
    * tally        — not triggered by cron (file-upload only)
    """
    from app.models.enums import IntegrationProvider
    from app.services.integrations import quickbooks, razorpay_invoices, zoho

    async with advisory_lock(db, ERP_SYNC_LOCK) as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "ERP sync already running for this business"}

        results: dict[str, dict] = {}

        # --- Zoho Books ---
        zoho_cred = await repository.get_integration_credential(
            db, tenant.business_id, IntegrationProvider.ZOHO_BOOKS
        )
        if zoho_cred and zoho_cred.refresh_token:
            try:
                org_id = (zoho_cred.extra or {}).get("org_id", "")
                result = await zoho.sync(
                    db,
                    tenant.business_id,
                    org_id=org_id,
                    access_token=zoho_cred.access_token,
                    refresh_token=zoho_cred.refresh_token,
                    client_id=settings.ZOHO_CLIENT_ID,
                    client_secret=settings.ZOHO_CLIENT_SECRET,
                    lookback_days=settings.ERP_SYNC_LOOKBACK_DAYS,
                )
                await repository.update_sync_stats(
                    db,
                    zoho_cred,
                    invoices_synced=result.invoices_created + result.invoices_updated,
                    errors=len(result.errors),
                )
                results["zoho_books"] = {
                    "invoices_created": result.invoices_created,
                    "invoices_updated": result.invoices_updated,
                    "errors": result.errors,
                }
            except Exception as exc:
                results["zoho_books"] = {"error": str(exc)}

        # --- QuickBooks ---
        qbo_cred = await repository.get_integration_credential(
            db, tenant.business_id, IntegrationProvider.QUICKBOOKS
        )
        if qbo_cred and qbo_cred.refresh_token:
            try:
                realm_id = (qbo_cred.extra or {}).get("realm_id", "")
                environment = (qbo_cred.extra or {}).get("environment", settings.QBO_ENVIRONMENT)
                result = await quickbooks.sync(
                    db,
                    tenant.business_id,
                    realm_id=realm_id,
                    access_token=qbo_cred.access_token,
                    refresh_token=qbo_cred.refresh_token,
                    client_id=settings.QBO_CLIENT_ID,
                    client_secret=settings.QBO_CLIENT_SECRET,
                    environment=environment,
                    lookback_days=settings.ERP_SYNC_LOOKBACK_DAYS,
                )
                await repository.update_sync_stats(
                    db,
                    qbo_cred,
                    invoices_synced=result.invoices_created + result.invoices_updated,
                    errors=len(result.errors),
                )
                results["quickbooks"] = {
                    "invoices_created": result.invoices_created,
                    "invoices_updated": result.invoices_updated,
                    "errors": result.errors,
                }
            except Exception as exc:
                results["quickbooks"] = {"error": str(exc)}

        # --- Razorpay Invoices ---
        try:
            result = await razorpay_invoices.sync(
                db,
                tenant.business_id,
                key_id=settings.RAZORPAY_KEY_ID,
                key_secret=settings.RAZORPAY_KEY_SECRET,
                lookback_days=settings.ERP_SYNC_LOOKBACK_DAYS,
            )
            rzp_cred = await repository.upsert_integration_credential(
                db, tenant.business_id, IntegrationProvider.RAZORPAY_INVOICES
            )
            await repository.update_sync_stats(
                db,
                rzp_cred,
                invoices_synced=result.invoices_created + result.invoices_updated,
                errors=len(result.errors),
            )
            results["razorpay_invoices"] = {
                "invoices_created": result.invoices_created,
                "invoices_updated": result.invoices_updated,
                "errors": result.errors,
            }
        except Exception as exc:
            results["razorpay_invoices"] = {"error": str(exc)}

        await db.commit()

    return {
        "status": "completed",
        "business_id": tenant.business_id,
        "providers": results,
        "synced_at": utc_now().isoformat(),
    }
