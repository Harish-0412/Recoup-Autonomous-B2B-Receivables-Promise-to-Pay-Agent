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
        # invoice chaseable in *this* run, not the next one.
        checked, broken, kept = await sweep_promises(db, ledger=ledger)
        summary.promises_checked = checked
        summary.promises_broken = broken
        summary.promises_kept = kept

        await run_batch_cycle(
            db,
            limit=limit or settings.BATCH_MAX_INVOICES,
            sending_enabled=settings.SENDING_ENABLED,
            ledger=ledger,
            summary=summary,
            dry_run=dry_run,
        )

        await repository.persist_ledger(db, ledger)

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
        await repository.save_batch_run(db, record)
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
async def get_latest_run(db: AsyncSession = Depends(get_db)) -> RunSummary | None:
    """Retrieve the latest completed autonomous batch run with its live decision items."""
    record = await repository.get_latest_batch_run(db)
    if record is None:
        return None
    return _record_to_summary(record)


@router.get("/runs", response_model=list[RunSummary])
async def list_past_runs(
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[RunSummary]:
    """List historical autonomous runs, most recent first."""
    records = await repository.list_batch_runs(db, limit=limit)
    return [_record_to_summary(r) for r in records]


@router.get("/status")
async def task_status(db: AsyncSession = Depends(get_db)) -> dict:
    """What the agent currently believes about itself.

    Exists so the kill switch can be confirmed from outside the process. "Is
    sending actually off?" should be answerable without reading logs or
    trusting that a config change took effect.
    """

    settings = get_settings()
    open_invoices = await repository.list_open_invoices(db, limit=settings.BATCH_MAX_INVOICES)
    pending = await repository.pending_promises(db)
    for_review = await repository.replies_needing_review(db, limit=500)

    return {
        "business_id": settings.BUSINESS_ID,
        "sending_enabled": settings.SENDING_ENABLED,
        "dry_run": settings.DRY_RUN,
        "batch_max_invoices": settings.BATCH_MAX_INVOICES,
        "expected_interval_seconds": settings.SCHEDULER_INTERVAL_SECONDS,
        "open_invoices": len(open_invoices),
        "pending_promises": len(pending),
        "replies_awaiting_review": len(for_review),
    }
