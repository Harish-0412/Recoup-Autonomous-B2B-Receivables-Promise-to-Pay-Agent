"""Turning a batch run into the numbers this project is judged on.

The standard this module is written to: **the report must be able to make the
agent look bad.** A false intervention is counted as a cost, not omitted. Cases
the agent left alone are reported whether or not leaving them alone was right.
Compliance violations are counted from the ledger rather than from the
intention to comply.

The counterfactual matters and is stated rather than implied. The synthetic
generator samples ``recovered`` for every invoice *independently of anything
the agent does* -- it is the outcome under no intervention. So
``recovery_rate_of_flagged`` measures whether the agent chose to act on the
invoices that were going to be paid; it is a **targeting** measure, not a
causal one, and no field here claims the agent caused a recovery. Measuring
uplift would need a holdout arm this simulation does not have, so the report
does not report one rather than reporting a number it cannot support.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.agent import CycleResult
from app.core.audit import DecisionLedger
from app.models.enums import DecisionOutcome, EscalationState, InterventionTier


def format_inr(amount: float) -> str:
    """Render rupees the way an Indian finance team reads them.

    Above a crore, in crores; above a lakh, in lakhs; otherwise plain.
    """

    if abs(amount) >= 1_00_00_000:
        return f"Rs {amount / 1_00_00_000:.2f} Cr"
    if abs(amount) >= 1_00_000:
        return f"Rs {amount / 1_00_000:.1f}L"
    return f"Rs {amount:,.0f}"


class BatchReport(BaseModel):
    """The evaluation table for one batch run."""

    model_config = ConfigDict(protected_namespaces=())

    invoices_processed: int = 0
    total_overdue_value: float = 0.0

    #: Per-case, from each invoice's final cycle.
    flagged_for_intervention: int = 0
    left_alone: int = 0
    handed_off_to_human: int = 0

    #: Per-run, summed over every cycle and read from the decision trace.
    #: These count *messages and refusals*, not invoices, so across a
    #: multi-cycle run they are legitimately larger than the case counts above.
    interventions_executed: int = 0
    blocked_by_policy: int = 0
    cycles_run: int = 1

    #: Ground truth, available only in the synthetic demo.
    recovered_count: int | None = None
    recovered_value: float | None = None
    recovery_rate_of_flagged: float | None = None
    false_interventions: int | None = None
    correctly_left_alone: int | None = None
    missed_recoveries: int | None = None

    compliance_violations: int = 0
    ledger_entries: int = 0
    ledger_verified: bool = False

    policy_block_reasons: dict[str, int] = Field(default_factory=dict)
    tier_counts: dict[str, int] = Field(default_factory=dict)

    def render(self) -> str:
        """The fixed-width block the README and the pitch deck carry."""

        rows: list[tuple[str, str]] = [
            ("Invoices processed", f"{self.invoices_processed}"),
            ("Total overdue value", format_inr(self.total_overdue_value)),
            ("Decision cycles run", f"{self.cycles_run}"),
            ("Cases flagged for intervention", f"{self.flagged_for_intervention}"),
            ("Cases left alone", f"{self.left_alone}"),
            ("Cases handed off to a human", f"{self.handed_off_to_human}"),
            ("Interventions executed (all cycles)", f"{self.interventions_executed}"),
            ("Actions blocked by policy (all cycles)", f"{self.blocked_by_policy}"),
        ]

        if self.recovered_count is not None:
            rows.extend(
                [
                    ("Recovered (of flagged)", f"{self.recovered_count}"),
                    ("Recovered value", format_inr(self.recovered_value or 0.0)),
                    (
                        "Recovery rate (of flagged)",
                        f"{(self.recovery_rate_of_flagged or 0.0) * 100:.1f}%",
                    ),
                    ("False/unnecessary interventions", f"{self.false_interventions}"),
                    ("Cases correctly left alone", f"{self.correctly_left_alone}"),
                    ("Missed recoveries (left alone, unpaid)", f"{self.missed_recoveries}"),
                ]
            )

        rows.extend(
            [
                ("Compliance violations", f"{self.compliance_violations}"),
                ("Decision trace entries", f"{self.ledger_entries}"),
                ("Ledger hash chain verified", "yes" if self.ledger_verified else "NO"),
            ]
        )

        width = max(len(label) for label, _ in rows) + 2
        return "\n".join(f"{label + ':':<{width}} {value}" for label, value in rows)


def count_compliance_violations(ledger: DecisionLedger) -> int:
    """Count contacts that were sent after the gate refused them.

    Read from the decision trace rather than from the orchestrator's return
    values, so a bug in the orchestrator cannot suppress the number that would
    reveal it. The ledger records what the gate *said*, independently of what
    the caller then did about it.

    Order matters, and getting this wrong is easy. Across several cycles the
    same invoice is routinely blocked in one cycle and legitimately actioned in
    a later one -- that is the contact-frequency cap working exactly as
    intended, not a violation. So this walks the ledger in sequence and tracks
    the *most recent* policy verdict per invoice: a contact counts against us
    only when the verdict standing at that moment was a block.
    """

    last_verdict: dict[str, DecisionOutcome] = {}
    violations = 0

    for entry in ledger:
        if entry.event.startswith("policy:"):
            last_verdict[entry.invoice_id] = entry.outcome
        elif (
            entry.event.startswith("transition:")
            and entry.payload.get("trigger") in {"send_reminder", "escalate"}
            and last_verdict.get(entry.invoice_id) is DecisionOutcome.BLOCKED
        ):
            violations += 1

    return violations


def build_report(
    results: list[CycleResult],
    *,
    ledger: DecisionLedger,
    outcomes: dict[str, bool] | None = None,
    cycles_run: int = 1,
) -> BatchReport:
    """Aggregate a batch run into a report.

    ``outcomes`` maps invoice id to the ground-truth "was it recovered inside
    the horizon" label. It exists only for the synthetic demo; against real
    data the outcome-dependent rows are simply absent rather than guessed.
    """

    report = BatchReport(
        invoices_processed=len(results),
        total_overdue_value=sum(result.score.outstanding for result in results),
        cycles_run=max(cycles_run, 1),
        ledger_entries=len(ledger),
        ledger_verified=ledger.is_valid(),
    )

    for result in results:
        report.tier_counts[result.tier.value] = report.tier_counts.get(result.tier.value, 0) + 1

        if result.tier is InterventionTier.WAIT:
            report.left_alone += 1
        else:
            report.flagged_for_intervention += 1

        if result.state_after is EscalationState.HUMAN_HANDOFF:
            report.handed_off_to_human += 1

    # Totals come from the ledger, not from the results, for the same reason
    # the violation count does: the trace is the record of what the gate said
    # and what actually fired, across every cycle, and it does not depend on
    # the orchestrator reporting itself accurately.
    for entry in ledger:
        if entry.event.startswith("transition:") and entry.payload.get("trigger") in {
            "send_reminder",
            "escalate",
        }:
            report.interventions_executed += 1
        elif entry.event.startswith("policy:") and entry.outcome is DecisionOutcome.BLOCKED:
            report.blocked_by_policy += 1
            for code in entry.payload.get("violations", []):
                report.policy_block_reasons[code] = report.policy_block_reasons.get(code, 0) + 1

    report.compliance_violations = count_compliance_violations(ledger)

    # Invoices the agent actually contacted at least once, over the whole run.
    # Using the final cycle's result instead would miss a case that was
    # contacted in cycle 1 and blocked in cycle 4, and so would under-report
    # exactly the cost this report exists to be honest about.
    ever_contacted = {
        entry.invoice_id
        for entry in ledger
        if entry.event.startswith("transition:")
        and entry.payload.get("trigger") in {"send_reminder", "escalate"}
    }

    if outcomes is not None:
        recovered = 0
        recovered_value = 0.0
        false_interventions = 0
        correctly_left_alone = 0
        missed = 0

        for result in results:
            was_recovered = outcomes.get(result.invoice_id, False)
            if result.tier is InterventionTier.WAIT:
                if was_recovered:
                    correctly_left_alone += 1
                else:
                    missed += 1
                continue

            if was_recovered:
                recovered += 1
                recovered_value += result.score.outstanding
            # A "false intervention" is contacting a customer whose invoice was
            # going to be recovered anyway. Under the generator's independent
            # sampling that is exactly an intervened case with a positive
            # label, which is why this number is large and honest rather than
            # small and flattering.
            if was_recovered and result.invoice_id in ever_contacted:
                false_interventions += 1

        report.recovered_count = recovered
        report.recovered_value = recovered_value
        report.recovery_rate_of_flagged = (
            recovered / report.flagged_for_intervention if report.flagged_for_intervention else 0.0
        )
        report.false_interventions = false_interventions
        report.correctly_left_alone = correctly_left_alone
        report.missed_recoveries = missed

    return report
