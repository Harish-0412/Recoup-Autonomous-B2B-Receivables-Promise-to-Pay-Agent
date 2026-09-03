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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import DecisionLedger
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import require_task_key
from app.db.session import get_db
from app.services import repository
from app.services.batch_runner import BATCH_LOCK, RunSummary, run_batch_cycle, sweep_promises
from app.services.locks import advisory_lock
from src.ml.versioning import utc_now

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_task_key)],
)
logger = get_logger(__name__)


@router.post("/run-batch", response_model=RunSummary)
async def run_batch(
    limit: int | None = Query(default=None, gt=0, description="Override the batch cap"),
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
        )

        await repository.persist_ledger(db, ledger)
        await db.commit()

    summary.finished_at = utc_now().isoformat()
    logger.info(
        "Batch run complete",
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
    )
    return summary


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
        "sending_enabled": settings.SENDING_ENABLED,
        "dry_run": settings.DRY_RUN,
        "batch_max_invoices": settings.BATCH_MAX_INVOICES,
        "expected_interval_seconds": settings.SCHEDULER_INTERVAL_SECONDS,
        "open_invoices": len(open_invoices),
        "pending_promises": len(pending),
        "replies_awaiting_review": len(for_review),
    }
