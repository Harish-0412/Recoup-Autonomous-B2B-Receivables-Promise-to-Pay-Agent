"""Nightly warehouse ETL: real recovery labels from the allocation ledger.

    python scripts/etl/real_outcomes.py [--business-id acme] [--horizon-days 30]

For every ``scored`` decision-trace event whose 30-day window has closed:

    recovered_within_30d = 1
        iff SUM(payment_allocations.amount
                where received_at <= scored_at + 30d)
            >= invoice.amount - epsilon

``scored_at`` is the trace's ``recorded_at`` evaluation timestamp, never the
due date. The join stays inside one ``business_id``: allocations, invoice and
trace must all belong to the same tenant. Invoices whose window has not closed
are excluded -- labelling them 0 would teach the model that recent invoices
fail (the same rule as the synthetic recovery card).

Features come frozen from the trace payload (``features`` written by
``run_cycle``), so rows are point-in-time by construction -- no history
reconstruction, no leakage. Rows whose stored feature set is not
``recovery-features-v1`` are skipped.

Output: ``data/warehouse/recovery_outcomes.parquet`` plus a JSON summary next
to it. The trainer consumes it via
``python scripts/train_recovery_model.py --source=warehouse``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402
from src.ml.features.recovery_features import (  # noqa: E402
    FEATURE_COLUMNS_V1,
    FEATURE_SET_VERSION,
)

logger = get_logger(__name__)

WAREHOUSE_DIR = REPO_ROOT / "data" / "warehouse"
OUT_PARQUET = WAREHOUSE_DIR / "recovery_outcomes.parquet"
OUT_SUMMARY = WAREHOUSE_DIR / "recovery_outcomes_summary.json"

#: Settlement tolerance in rupees. Matches the PAID derivation rule
#: (amount_paid + 0.01 >= amount): a fully settled invoice labels 1 even with
#: paise-level rounding or a 1-paisa-short bank settlement.
EPSILON_INR = 0.01


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _tenant_list(session, only: str | None) -> list[str]:
    if only:
        return [only]
    from sqlalchemy import select as sa_select

    from app.models import Business as BusinessModel

    try:
        rows = await session.execute(sa_select(BusinessModel.business_id))
        tenants = [row for (row,) in rows.all()]
        if tenants:
            return tenants
    except Exception:
        pass
    return [get_settings().BUSINESS_ID or "default"]


async def extract(*, horizon_days: int = 30, business_id: str | None = None) -> dict:
    """Build the outcomes frame. Returns summary counts (also written to disk)."""
    from sqlalchemy import select as sa_select

    from app.models import (
        Customer,
        DecisionTrace,
        Invoice,
        PaymentAllocation,
    )

    now = datetime.now(UTC)
    horizon = timedelta(days=horizon_days)
    rows: list[dict] = []
    counts = {
        "scored_events": 0,
        "mature_rows": 0,
        "immature_excluded": 0,
        "missing_features_skipped": 0,
        "unmatched_invoice_skipped": 0,
        "positive_rate": 0.0,
    }

    async with async_session_maker() as session:
        tenants = await _tenant_list(session, business_id)
        for tenant in tenants:
            traces = (
                (
                    await session.execute(
                        sa_select(DecisionTrace)
                        .where(
                            DecisionTrace.business_id == tenant,
                            DecisionTrace.event == "scored",
                        )
                        .order_by(DecisionTrace.recorded_at)
                    )
                )
                .scalars()
                .all()
            )

            for trace in traces:
                counts["scored_events"] += 1
                scored_at = _as_utc(trace.recorded_at)
                if scored_at + horizon > now:
                    counts["immature_excluded"] += 1
                    continue
                payload = trace.payload or {}
                if payload.get("feature_set") != FEATURE_SET_VERSION:
                    counts["missing_features_skipped"] += 1
                    continue
                features = payload.get("features") or {}
                if set(features) != set(FEATURE_COLUMNS_V1):
                    counts["missing_features_skipped"] += 1
                    continue

                invoice = (
                    await session.execute(
                        sa_select(Invoice).where(
                            Invoice.business_id == tenant,
                            Invoice.invoice_id == trace.invoice_id,
                        )
                    )
                ).scalar_one_or_none()
                if invoice is None:
                    counts["unmatched_invoice_skipped"] += 1
                    continue

                window_end = scored_at + horizon
                allocs = (
                    await session.execute(
                        sa_select(PaymentAllocation.amount).where(
                            PaymentAllocation.business_id == tenant,
                            PaymentAllocation.invoice_pk == invoice.id,
                            PaymentAllocation.received_at <= window_end,
                        )
                    )
                ).all()
                settled = float(sum(amount for (amount,) in allocs))
                label = 1 if settled >= invoice.amount - EPSILON_INR else 0

                customer = await session.get(Customer, invoice.customer_pk)
                rows.append(
                    {
                        "business_id": tenant,
                        "invoice_id": trace.invoice_id,
                        "customer_id": customer.customer_id if customer else "",
                        "scored_at": scored_at.isoformat(),
                        "window_days": horizon_days,
                        "invoice_amount": float(invoice.amount),
                        "settled_in_window": round(settled, 2),
                        "recovered_within_30d": label,
                        **{col: float(features[col]) for col in FEATURE_COLUMNS_V1},
                    }
                )
                counts["mature_rows"] += 1

    counts["positive_rate"] = round(
        sum(r["recovered_within_30d"] for r in rows) / max(len(rows), 1), 4
    )
    counts["businesses"] = tenants
    counts["horizon_days"] = horizon_days
    counts["label_rule"] = (
        "SUM(payment_allocations.amount WHERE received_at <= scored_at + horizon) "
        ">= invoice.amount - 0.01, same business_id"
    )

    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    import pandas as pd  # noqa: F811

    frame = pd.DataFrame(rows)
    frame.to_parquet(OUT_PARQUET, index=False)
    OUT_SUMMARY.write_text(json.dumps(counts, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "Real outcomes ETL complete",
        mature_rows=counts["mature_rows"],
        positive_rate=counts["positive_rate"],
        immature_excluded=counts["immature_excluded"],
        out=str(OUT_PARQUET),
    )
    print(
        f"mature_rows={counts['mature_rows']} "
        f"positive_rate={counts['positive_rate']} "
        f"immature_excluded={counts['immature_excluded']} "
        f"-> {OUT_PARQUET}"
    )
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-days", type=int, default=30)
    parser.add_argument("--business-id", type=str, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    asyncio.run(extract(horizon_days=args.horizon_days, business_id=args.business_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
