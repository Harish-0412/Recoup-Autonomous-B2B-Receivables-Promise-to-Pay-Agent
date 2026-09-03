"""The autonomous run: what happens when the cron fires.

This is the piece that makes the word "autonomous" in the project title true.
Until now the agent decided only when a human called an endpoint.

Two sweeps, in this order, because the order matters:

1. **Promise sweep.** Promises whose date has passed unpaid are marked broken.
   That is what re-arms escalation: the policy gate stays quiet while a promise
   is open, so a promise nobody ever marked broken silences the agent on that
   invoice permanently. Running it *before* scoring means an invoice whose
   promise lapsed this morning is chaseable this run rather than next.
2. **Decision cycles.** Score, gate, transition, execute -- the same path the
   single-invoice endpoint uses, over a bounded slice of the open book.

Everything below assumes it holds the batch lock. Acquiring it is the caller's
job, because "what should happen when another run is already going" is a
routing decision, not a domain one.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent import AgentConfig, run_cycle
from app.core.audit import DecisionLedger, append_decision_trace
from app.core.logging import get_logger
from app.core.policy import policy_config_from_settings
from app.core.promise_tracker import PromiseRecord, assess_promise
from app.models import DecisionOutcome, InterventionTier, InvoiceStatus, PromiseStatus
from app.services import repository
from app.services.executor import ExecutionIntent, build_execution_service
from src.ml.versioning import utc_now

logger = get_logger(__name__)

#: The lock every batch run contends for. One name, so a promise sweep and a
#: decision run cannot interleave on the same book either.
BATCH_LOCK = "recoup:batch-run"


class RunSummary(BaseModel):
    """What one triggered run did.

    Reported rather than merely logged, because the cron trigger is the only
    caller and its logs are the only place anyone would otherwise look.
    """

    model_config = ConfigDict(protected_namespaces=())

    started_at: str
    finished_at: str = ""
    #: False when another run held the lock. Everything else is then zero.
    ran: bool = True
    skipped_reason: str = ""

    #: Promise sweep
    promises_checked: int = 0
    promises_broken: int = 0
    promises_kept: int = 0

    #: Decision cycles
    invoices_considered: int = 0
    scored: int = 0
    acted: int = 0
    blocked_by_policy: int = 0
    left_alone: int = 0
    delivery_failed: int = 0
    handed_off: int = 0

    #: True when the kill switch was off, so nothing was sent this run.
    sending_halted: bool = False
    errors: list[str] = Field(default_factory=list)


async def sweep_promises(
    session: AsyncSession,
    *,
    as_of: date | None = None,
    ledger: DecisionLedger | None = None,
) -> tuple[int, int, int]:
    """Resolve promises whose date has passed. Returns (checked, broken, kept).

    A promise is only ever settled by money that actually arrived --
    ``amount_paid`` is written by the payment webhook and by nothing else. This
    function asks the ledger, never the customer.
    """

    reference = as_of or utc_now().date()
    checked = broken = kept = 0

    for promise_row, invoice in await repository.pending_promises(session):
        checked += 1
        outcome = assess_promise(
            PromiseRecord(
                promise_id=promise_row.promise_id,
                invoice_id=invoice.invoice_id,
                promised_amount=promise_row.promised_amount,
                promised_date=promise_row.promised_date,
                currency=promise_row.currency,
                source_reply_id=promise_row.source_reply_id,
                source_confidence=promise_row.source_confidence,
                status=promise_row.status,
                created_at=promise_row.created_at,
            ),
            amount_paid=invoice.amount_paid,
            as_of=reference,
            ledger=ledger,
        )

        if outcome.status is PromiseStatus.PENDING:
            continue

        promise_row.status = outcome.status
        promise_row.resolved_at = utc_now()

        if outcome.status is PromiseStatus.KEPT:
            kept += 1
            continue

        broken += 1
        # Releasing the invoice from PROMISED is the point of the whole sweep:
        # the policy gate goes quiet while a promise is open, so an unbroken
        # lapsed promise silences the agent on that invoice forever.
        if invoice.status is InvoiceStatus.PROMISED:
            invoice.status = InvoiceStatus.IN_PROGRESS

    return checked, broken, kept


async def run_batch_cycle(
    session: AsyncSession,
    *,
    limit: int,
    sending_enabled: bool,
    ledger: DecisionLedger,
    summary: RunSummary,
) -> None:
    """Run one decision cycle over a bounded slice of the open book."""

    settings = AgentConfig(policy=policy_config_from_settings())
    executor = build_execution_service()

    invoices = await repository.list_open_invoices(session, limit=limit)
    summary.invoices_considered = len(invoices)

    for invoice in invoices:
        try:
            case = await repository.load_case(session, invoice)
            result = run_cycle(case, config=settings, ledger=ledger)
            summary.scored += 1

            if result.tier is InterventionTier.WAIT:
                summary.left_alone += 1
                continue
            if result.decision is not None and not result.decision.allowed:
                summary.blocked_by_policy += 1
                continue
            if result.action is None or result.decision is None or not result.acted:
                continue

            if not result.action.is_contact:
                # HAND_OFF and CLOSE move internal state and send nothing.
                invoice.escalation_state = result.state_after
                invoice.ladder_index += 1
                summary.handed_off += 1
                continue

            if not sending_enabled:
                # The kill switch. Nothing is sent and nothing advances, so
                # turning it back on resumes where the agent left off rather
                # than finding every invoice a rung further along.
                summary.sending_halted = True
                append_decision_trace(
                    invoice_id=invoice.invoice_id,
                    event="execution:halted",
                    outcome=DecisionOutcome.SKIPPED,
                    reason="SENDING_ENABLED is false; outbound contact is halted.",
                    ledger=ledger,
                    ladder_step=result.ladder_step,
                )
                continue

            intent = ExecutionIntent.from_decision(case, result.action, result.decision)
            execution = await executor.execute(session, intent, invoice)

            append_decision_trace(
                invoice_id=invoice.invoice_id,
                event="executed",
                outcome=(
                    DecisionOutcome.EXECUTED if execution.delivered else DecisionOutcome.FAILED
                ),
                reason=(
                    f"{execution.status.value} via {execution.channel.value}"
                    if execution.delivered
                    else f"delivery failed: {execution.error}"
                ),
                ledger=ledger,
                ladder_step=execution.ladder_step,
                provider_message_id=execution.provider_message_id,
                payment_link_id=execution.payment_link_id,
            )

            if execution.delivered:
                invoice.escalation_state = result.state_after
                invoice.ladder_index += 1
                summary.acted += 1
            else:
                # Same rule as the single-invoice path: a failed send buys no
                # rung, so the next run retries this one rather than marching
                # the invoice toward final notice on undelivered mail.
                summary.delivery_failed += 1

        except Exception as exc:  # noqa: BLE001 - one bad invoice must not end the run
            # A batch that dies on invoice 40 of 200 leaves the other 160
            # untouched with no record of why.
            summary.errors.append(f"{invoice.invoice_id}: {type(exc).__name__}: {exc}")
            logger.error(
                "Invoice failed during batch run",
                invoice_id=invoice.invoice_id,
                error=str(exc),
            )
