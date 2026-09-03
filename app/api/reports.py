"""The batch evaluation report, over whatever is currently in the database.

This runs the agent in **dry-run**: every invoice is scored and gated, and the
results are aggregated, but nothing is sent and no state is persisted. That
distinction matters -- a reporting endpoint that quietly advanced the ladder
every time someone refreshed a dashboard would be a genuinely dangerous thing
to have in a collections system.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent import AgentConfig, run_batch
from app.core.audit import DecisionLedger
from app.core.evaluation import build_report, format_inr
from app.core.policy import policy_config_from_settings
from app.db.session import get_db
from app.services import repository

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/batch")
async def batch_report(
    limit: int = Query(default=500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Score and gate every open invoice, and return the evaluation table.

    Dry run: the ledger built here is local to the request and is deliberately
    **not** persisted, so calling this does not write decisions the agent did
    not actually take.
    """

    invoices = await repository.list_open_invoices(db, limit=limit)
    cases = [await repository.load_case(db, invoice) for invoice in invoices]

    ledger = DecisionLedger()
    results = run_batch(
        cases,
        config=AgentConfig(policy=policy_config_from_settings()),
        ledger=ledger,
    )
    report = build_report(results, ledger=ledger)

    return {
        "dry_run": True,
        "note": (
            "Scored and gated in memory. No messages were sent, no invoice "
            "state changed, and these decisions were not written to the "
            "decision trace."
        ),
        "report": report.model_dump(),
        "rendered": report.render(),
        "total_overdue_value_formatted": format_inr(report.total_overdue_value),
        "top_cases": [
            {
                "invoice_id": result.invoice_id,
                "outstanding": result.score.outstanding,
                "p_recovery": result.score.p_recovery,
                "expected_value": result.score.expected_value,
                "tier": result.tier.value,
                "rationale": result.score.rationale,
                "policy_allowed": result.decision.allowed if result.decision else None,
                "reason": result.reason,
            }
            for result in results[:20]
        ],
    }
