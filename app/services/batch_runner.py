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
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent import AgentConfig, run_cycle
from app.core.audit import DecisionLedger, append_decision_trace
from app.core.logging import get_logger
from app.core.policy import policy_config_from_settings
from app.core.promise_tracker import PromiseRecord, assess_promise
from app.models import (
    DecisionOutcome,
    EscalationState,
    InterventionTier,
    Invoice,
    InvoiceStatus,
    PromiseStatus,
)
from app.services import repository
from app.services.executor import ExecutionIntent, build_execution_service
from src.ml.versioning import utc_now

logger = get_logger(__name__)

#: The lock every batch run contends for. One name, so a promise sweep and a
#: decision run cannot interleave on the same book either.
BATCH_LOCK = "recoup:batch-run"


class ExpectedFollowup(BaseModel):
    """What the autonomous system expects next and how it will follow up."""

    model_config = ConfigDict(protected_namespaces=())

    expectation: str
    next_action: str
    timeline: str
    action_owner: str


class RunInvoiceDecision(BaseModel):
    """Item-level decision audit for a single invoice processed in a run."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    customer_id: str
    customer_name: str
    amount: float
    amount_paid: float
    outstanding: float
    currency: str = "INR"
    days_overdue: int

    # ML / Scorer
    tier: str  # WAIT | REMIND | ESCALATE
    p_recovery: float
    expected_value: float
    expected_recovery: float = 0.0
    rationale: str

    # Proposed & Policy Gate
    action_type: str | None = None
    ladder_step: str = ""
    decision_allowed: bool | None = None
    decision_reason: str
    violations: list[dict[str, str]] = Field(default_factory=list)
    effective_discount_pct: float = 0.0
    effective_discount_amount: float = 0.0

    # State transition
    state_before: str
    state_after: str
    transitioned: bool

    # Execution
    executed: bool = False
    execution_status: str | None = None  # delivered, simulated, halted, failed, skipped
    channel: str | None = None
    subject: str | None = None
    body_preview: str | None = None
    payment_link_url: str | None = None

    # Next expected follow-up
    expected_followup: ExpectedFollowup


class RunSummary(BaseModel):
    """What one triggered run did, including per-invoice decisions.

    Reported rather than merely logged, because the cron trigger is the only
    caller and its logs are the only place anyone would otherwise look.
    """

    model_config = ConfigDict(protected_namespaces=())

    run_id: str = ""
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

    #: Live per-invoice breakdown
    invoice_decisions: list[RunInvoiceDecision] = Field(default_factory=list)


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


def derive_expected_followup(
    invoice: Invoice,
    result: Any,
    execution: Any | None,
    sending_enabled: bool,
) -> ExpectedFollowup:
    """Derive clear operational expectations and follow-up timeline for an invoice."""
    if result.tier is InterventionTier.WAIT:
        return ExpectedFollowup(
            expectation=f"Self-cure expected: recovery probability {result.score.p_recovery:.1%} clears the self-cure threshold (0.95).",
            next_action="Outreach withheld to protect customer goodwill and avoid unnecessary nudging. Invoice remains in passive monitoring.",
            timeline="Re-scored on next scheduler cycle (5 min)",
            action_owner="Autonomous Agent (Passive Monitoring)",
        )

    if result.decision is not None and not result.decision.allowed:
        first_code = (
            result.decision.violations[0].code if result.decision.violations else "policy_guard"
        )
        if "frequency" in first_code or "gap" in first_code:
            exp = "Contact frequency cap active: minimum 3-day quiet period between contacts is required."
            act = "System pauses outreach until the mandatory cooling-off window elapses."
            time_str = "After minimum 3-day gap elapses"
        elif "volume" in first_code:
            exp = "Maximum volume ceiling reached (4 outreach contacts sent for this invoice)."
            act = "Autonomous outreach stopped permanently to prevent customer harassment. Awaiting manual collections."
            time_str = "Indefinite (Cap reached)"
        elif "promise" in first_code:
            exp = "Undue promise-to-pay is active. Customer has committed to pay before deadline."
            act = "System stays quiet until promise date. Awaiting payment receipt via Razorpay webhook."
            time_str = "Until promise date passes"
        elif "overdue" in first_code:
            exp = "Invoice is in early grace period (not yet past minimum overdue threshold)."
            act = (
                "No outreach dispatched. Will re-evaluate once invoice matures past the threshold."
            )
            time_str = "Next scheduler cycle (5 min)"
        else:
            exp = f"Action blocked by deterministic policy gate: {result.decision.reason}."
            act = "System re-checks policy constraints on every cron cycle and acts when clear."
            time_str = "Next scheduler cycle (5 min)"

        return ExpectedFollowup(
            expectation=exp,
            next_action=act,
            timeline=time_str,
            action_owner="Policy Engine (Deterministic Ceiling)",
        )

    if not sending_enabled and result.action is not None and result.action.is_contact:
        return ExpectedFollowup(
            expectation="Operational kill switch is active (SENDING_ENABLED=false). Outreach halted by operator.",
            next_action="Cycle scored and gated without advancing ladder rung. Will dispatch live once kill switch is enabled.",
            timeline="Pending SENDING_ENABLED=true toggle",
            action_owner="Operations Control (Kill Switch)",
        )

    if (
        (result.action is not None and not result.action.is_contact)
        or result.state_after is EscalationState.HUMAN_HANDOFF
        or result.terminal
    ):
        return ExpectedFollowup(
            expectation="Escalation ladder exhausted or legal dispute limit reached.",
            next_action="Case escalated to Human Collections desk. Autonomous agent steps aside for human review.",
            timeline="Immediate (queued in /inbox review desk)",
            action_owner="Human Credit Officer",
        )

    if execution is not None:
        channel_name = execution.channel.value if hasattr(execution, "channel") else "email"
        step_title = (result.ladder_step or "reminder").replace("_", " ").title()
        if execution.delivered:
            if result.tier is InterventionTier.ESCALATE:
                waiver_info = ""
                if result.decision and result.decision.effective_discount_pct > 0:
                    waiver_info = f" with ₹{result.decision.effective_discount_amount:,.0f} ({result.decision.effective_discount_pct:g}%) settlement waiver"
                return ExpectedFollowup(
                    expectation=f"Final Notice delivered via {channel_name}{waiver_info}. Expecting customer settlement via Razorpay link or reply.",
                    next_action="If customer settles via Razorpay link, webhook clears invoice to PAID. If unpaid after window, agent moves case to Human Handoff.",
                    timeline="72-hour settlement response window",
                    action_owner="Customer / Razorpay Gateway",
                )
            else:
                return ExpectedFollowup(
                    expectation=f"{step_title} delivered via {channel_name} with Razorpay payment link. Expecting payment or reply.",
                    next_action="System monitors for Razorpay payment webhook. If unpaid after 3-day gap, will advance to next ladder rung.",
                    timeline="3-day contact frequency window",
                    action_owner="Customer / Razorpay Gateway",
                )
        else:
            return ExpectedFollowup(
                expectation=f"Outbound dispatch failed: {execution.error or 'provider error'}.",
                next_action="Ladder rung was NOT advanced. Next cycle will retry this rung rather than advancing on undelivered notice.",
                timeline="Next scheduler cycle retry",
                action_owner="Autonomous Agent (Delivery Retry)",
            )

    return ExpectedFollowup(
        expectation=f"Current case state: {invoice.escalation_state.value}. System monitoring invoice.",
        next_action="Autonomous agent will evaluate next ladder step on subsequent trigger.",
        timeline="Next scheduler cycle (5 min)",
        action_owner="Autonomous Agent",
    )


async def run_batch_cycle(
    session: AsyncSession,
    *,
    limit: int,
    sending_enabled: bool,
    ledger: DecisionLedger,
    summary: RunSummary,
    dry_run: bool | None = None,
) -> None:
    """Run one decision cycle over a bounded slice of the open book."""

    settings = AgentConfig(policy=policy_config_from_settings())
    executor = build_execution_service(dry_run=dry_run)
    today = utc_now().date()

    if not summary.run_id:
        summary.run_id = f"run_{utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"

    invoices = await repository.list_open_invoices(session, limit=limit)
    summary.invoices_considered = len(invoices)

    for invoice in invoices:
        try:
            case = await repository.load_case(session, invoice)
            result = run_cycle(case, config=settings, ledger=ledger)
            summary.scored += 1

            days_overdue = max((today - invoice.due_date).days, 0)
            outstanding = max(invoice.amount - invoice.amount_paid, 0.0)

            # 1. WAIT: Self-cure candidate
            if result.tier is InterventionTier.WAIT:
                summary.left_alone += 1
                followup = derive_expected_followup(invoice, result, None, sending_enabled)
                summary.invoice_decisions.append(
                    RunInvoiceDecision(
                        invoice_id=invoice.invoice_id,
                        customer_id=case.customer.customer_id,
                        customer_name=case.customer.name,
                        amount=invoice.amount,
                        amount_paid=invoice.amount_paid,
                        outstanding=outstanding,
                        currency=invoice.currency,
                        days_overdue=days_overdue,
                        tier=result.tier.value,
                        p_recovery=result.score.p_recovery,
                        expected_value=result.score.expected_value,
                        expected_recovery=result.score.expected_recovery,
                        rationale=result.score.rationale,
                        action_type=None,
                        ladder_step=result.ladder_step or "none",
                        decision_allowed=None,
                        decision_reason=result.reason
                        or "Self-cure candidate; intervention suppressed to protect goodwill.",
                        violations=[],
                        effective_discount_pct=0.0,
                        effective_discount_amount=0.0,
                        state_before=result.state_before.value,
                        state_after=result.state_after.value,
                        transitioned=False,
                        executed=False,
                        execution_status="left_alone",
                        channel=None,
                        subject=None,
                        body_preview=None,
                        payment_link_url=None,
                        expected_followup=followup,
                    )
                )
                continue

            # 2. Blocked by Policy Gate
            if result.decision is not None and not result.decision.allowed:
                summary.blocked_by_policy += 1
                followup = derive_expected_followup(invoice, result, None, sending_enabled)
                violations = [
                    {"code": v.code, "message": v.message} for v in result.decision.violations
                ]
                summary.invoice_decisions.append(
                    RunInvoiceDecision(
                        invoice_id=invoice.invoice_id,
                        customer_id=case.customer.customer_id,
                        customer_name=case.customer.name,
                        amount=invoice.amount,
                        amount_paid=invoice.amount_paid,
                        outstanding=outstanding,
                        currency=invoice.currency,
                        days_overdue=days_overdue,
                        tier=result.tier.value,
                        p_recovery=result.score.p_recovery,
                        expected_value=result.score.expected_value,
                        expected_recovery=result.score.expected_recovery,
                        rationale=result.score.rationale,
                        action_type=result.action.action_type.value if result.action else None,
                        ladder_step=result.ladder_step or "none",
                        decision_allowed=False,
                        decision_reason=result.decision.reason,
                        violations=violations,
                        effective_discount_pct=result.decision.effective_discount_pct,
                        effective_discount_amount=result.decision.effective_discount_amount,
                        state_before=result.state_before.value,
                        state_after=result.state_before.value,
                        transitioned=False,
                        executed=False,
                        execution_status="blocked_by_policy",
                        channel=None,
                        subject=None,
                        body_preview=None,
                        payment_link_url=None,
                        expected_followup=followup,
                    )
                )
                continue

            # 3. Terminal or non-acted case (e.g. human handoff or FSM guard stopped move)
            if result.action is None or result.decision is None or not result.acted:
                if result.state_after is EscalationState.HUMAN_HANDOFF or result.terminal:
                    summary.handed_off += 1
                followup = derive_expected_followup(invoice, result, None, sending_enabled)
                summary.invoice_decisions.append(
                    RunInvoiceDecision(
                        invoice_id=invoice.invoice_id,
                        customer_id=case.customer.customer_id,
                        customer_name=case.customer.name,
                        amount=invoice.amount,
                        amount_paid=invoice.amount_paid,
                        outstanding=outstanding,
                        currency=invoice.currency,
                        days_overdue=days_overdue,
                        tier=result.tier.value,
                        p_recovery=result.score.p_recovery,
                        expected_value=result.score.expected_value,
                        expected_recovery=result.score.expected_recovery,
                        rationale=result.score.rationale,
                        action_type=result.action.action_type.value if result.action else None,
                        ladder_step=result.ladder_step or "human_handoff",
                        decision_allowed=result.decision.allowed if result.decision else None,
                        decision_reason=result.reason
                        or f"Case is {result.state_before.value}; no automated action remains.",
                        violations=[],
                        effective_discount_pct=0.0,
                        effective_discount_amount=0.0,
                        state_before=result.state_before.value,
                        state_after=result.state_after.value,
                        transitioned=result.transitioned,
                        executed=False,
                        execution_status="handed_off"
                        if result.state_after is EscalationState.HUMAN_HANDOFF
                        else "skipped",
                        channel=None,
                        subject=None,
                        body_preview=None,
                        payment_link_url=None,
                        expected_followup=followup,
                    )
                )
                continue

            # 4. Non-contact actions (HAND_OFF, CLOSE)
            if not result.action.is_contact:
                invoice.escalation_state = result.state_after
                invoice.ladder_index += 1
                summary.handed_off += 1
                followup = derive_expected_followup(invoice, result, None, sending_enabled)
                summary.invoice_decisions.append(
                    RunInvoiceDecision(
                        invoice_id=invoice.invoice_id,
                        customer_id=case.customer.customer_id,
                        customer_name=case.customer.name,
                        amount=invoice.amount,
                        amount_paid=invoice.amount_paid,
                        outstanding=outstanding,
                        currency=invoice.currency,
                        days_overdue=days_overdue,
                        tier=result.tier.value,
                        p_recovery=result.score.p_recovery,
                        expected_value=result.score.expected_value,
                        expected_recovery=result.score.expected_recovery,
                        rationale=result.score.rationale,
                        action_type=result.action.action_type.value,
                        ladder_step=result.ladder_step,
                        decision_allowed=True,
                        decision_reason=result.decision.reason,
                        violations=[],
                        effective_discount_pct=result.decision.effective_discount_pct,
                        effective_discount_amount=result.decision.effective_discount_amount,
                        state_before=result.state_before.value,
                        state_after=result.state_after.value,
                        transitioned=True,
                        executed=True,
                        execution_status="handed_off",
                        channel=None,
                        subject=None,
                        body_preview=None,
                        payment_link_url=None,
                        expected_followup=followup,
                    )
                )
                continue

            # 5. Kill Switch active: contact action halted
            if not sending_enabled:
                summary.sending_halted = True
                append_decision_trace(
                    invoice_id=invoice.invoice_id,
                    event="execution:halted",
                    outcome=DecisionOutcome.SKIPPED,
                    reason="SENDING_ENABLED is false; outbound contact is halted.",
                    ledger=ledger,
                    ladder_step=result.ladder_step,
                )
                followup = derive_expected_followup(invoice, result, None, sending_enabled)
                summary.invoice_decisions.append(
                    RunInvoiceDecision(
                        invoice_id=invoice.invoice_id,
                        customer_id=case.customer.customer_id,
                        customer_name=case.customer.name,
                        amount=invoice.amount,
                        amount_paid=invoice.amount_paid,
                        outstanding=outstanding,
                        currency=invoice.currency,
                        days_overdue=days_overdue,
                        tier=result.tier.value,
                        p_recovery=result.score.p_recovery,
                        expected_value=result.score.expected_value,
                        expected_recovery=result.score.expected_recovery,
                        rationale=result.score.rationale,
                        action_type=result.action.action_type.value,
                        ladder_step=result.ladder_step,
                        decision_allowed=True,
                        decision_reason=result.decision.reason,
                        violations=[],
                        effective_discount_pct=result.decision.effective_discount_pct,
                        effective_discount_amount=result.decision.effective_discount_amount,
                        state_before=result.state_before.value,
                        state_after=result.state_before.value,
                        transitioned=False,
                        executed=False,
                        execution_status="halted",
                        channel=None,
                        subject=None,
                        body_preview=None,
                        payment_link_url=None,
                        expected_followup=followup,
                    )
                )
                continue

            # 6. Execute outbound contact
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
                summary.delivery_failed += 1

            followup = derive_expected_followup(invoice, result, execution, sending_enabled)
            summary.invoice_decisions.append(
                RunInvoiceDecision(
                    invoice_id=invoice.invoice_id,
                    customer_id=case.customer.customer_id,
                    customer_name=case.customer.name,
                    amount=invoice.amount,
                    amount_paid=invoice.amount_paid,
                    outstanding=outstanding,
                    currency=invoice.currency,
                    days_overdue=days_overdue,
                    tier=result.tier.value,
                    p_recovery=result.score.p_recovery,
                    expected_value=result.score.expected_value,
                    expected_recovery=result.score.expected_recovery,
                    rationale=result.score.rationale,
                    action_type=result.action.action_type.value,
                    ladder_step=result.ladder_step,
                    decision_allowed=True,
                    decision_reason=result.decision.reason,
                    violations=[],
                    effective_discount_pct=result.decision.effective_discount_pct,
                    effective_discount_amount=result.decision.effective_discount_amount,
                    state_before=result.state_before.value,
                    state_after=result.state_after.value
                    if execution.delivered
                    else result.state_before.value,
                    transitioned=execution.delivered,
                    executed=True,
                    execution_status=execution.status.value,
                    channel=execution.channel.value,
                    subject=execution.subject,
                    body_preview=execution.body_preview,
                    payment_link_url=execution.payment_link_url,
                    expected_followup=followup,
                )
            )

        except Exception as exc:  # noqa: BLE001 - one bad invoice must not end the run
            summary.errors.append(f"{invoice.invoice_id}: {type(exc).__name__}: {exc}")
            logger.error(
                "Invoice failed during batch run",
                invoice_id=invoice.invoice_id,
                error=str(exc),
            )
