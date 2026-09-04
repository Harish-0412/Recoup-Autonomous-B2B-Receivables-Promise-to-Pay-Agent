"""Nightly ETL: real broken-promise labels from the promise tracker.

    python scripts/etl/real_broken_promise_outcomes.py [--business-id acme]

Labels come from promise_tracker outcomes recorded against settled money --
promise rows resolved KEPT/BROKEN by the webhook sweep against the allocation
ledger -- never from the 15k-row mock generator. Features are the frozen
19-vectors stored in ``promise:scored`` traces at record time, so rows are
point-in-time by construction.

Output schema matches the mock ETL exactly
(``promise_id, customer_id, *FEATURE_COLUMNS, is_broken``) so
``models/broken_promise/train.py`` consumes it unchanged via --data:
``data/broken_promise_live.csv``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402
from src.agent.promise_handler import FEATURE_COLUMNS  # noqa: E402

logger = get_logger(__name__)

OUT_CSV = REPO_ROOT / "data" / "broken_promise_live.csv"


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


async def extract(*, business_id: str | None = None) -> dict:
    """Build live labelled rows. Returns summary counts (also written to disk)."""
    from sqlalchemy import select as sa_select

    from app.models import Customer, DecisionTrace, Invoice, PaymentAllocation, Promise
    from app.models.enums import PromiseStatus

    counts = {
        "promise_scored_events": 0,
        "live_rows": 0,
        "broken_rate": 0.0,
        "pending_skipped": 0,
        "unmatched_skipped": 0,
    }
    out_rows: list[dict] = []

    async with async_session_maker() as session:
        tenants = await _tenant_list(session, business_id)
        for tenant in tenants:
            traces = (
                (
                    await session.execute(
                        sa_select(DecisionTrace).where(
                            DecisionTrace.business_id == tenant,
                            DecisionTrace.event == "promise:scored",
                        )
                    )
                )
                .scalars()
                .all()
            )

            for trace in traces:
                counts["promise_scored_events"] += 1
                payload = trace.payload or {}
                features = payload.get("features") or {}
                if set(features) != set(FEATURE_COLUMNS):
                    counts["unmatched_skipped"] += 1
                    continue
                promise_id = payload.get("promise_id")
                if not promise_id:
                    counts["unmatched_skipped"] += 1
                    continue

                promise = (
                    await session.execute(
                        sa_select(Promise).where(
                            Promise.business_id == tenant,
                            Promise.promise_id == promise_id,
                        )
                    )
                ).scalar_one_or_none()
                if promise is None or promise.status not in (
                    PromiseStatus.KEPT,
                    PromiseStatus.BROKEN,
                ):
                    counts["pending_skipped"] += 1
                    continue

                # Authoritative label: the tracker's own verdict, itself settled
                # against allocation money (webhook sweep), not classifier output.
                is_broken = 1 if promise.status is PromiseStatus.BROKEN else 0
                allocs = (
                    await session.execute(
                        sa_select(PaymentAllocation.amount).where(
                            PaymentAllocation.business_id == tenant,
                            PaymentAllocation.invoice_pk == promise.invoice_pk,
                        )
                    )
                ).all()
                settled = float(sum(amount for (amount,) in allocs))

                invoice = await session.get(Invoice, promise.invoice_pk)
                customer = (
                    await session.get(Customer, invoice.customer_pk)
                    if invoice is not None
                    else None
                )
                out_rows.append(
                    {
                        "promise_id": promise_id,
                        "customer_id": customer.customer_id if customer else "",
                        **{col: float(features[col]) for col in FEATURE_COLUMNS},
                        "is_broken": is_broken,
                        "settled_total": round(settled, 2),
                    }
                )
                counts["live_rows"] += 1

    counts["broken_rate"] = round(sum(r["is_broken"] for r in out_rows) / max(len(out_rows), 1), 4)
    counts["businesses"] = tenants
    counts["label_rule"] = "promise_tracker status (KEPT=0/BROKEN=1), resolved vs allocation ledger"

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    train_cols = ["promise_id", "customer_id", *FEATURE_COLUMNS, "is_broken"]
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=train_cols)
        writer.writeheader()
        for row in out_rows:
            writer.writerow({col: row[col] for col in train_cols})
    logger.info(
        "Broken-promise live ETL complete",
        live_rows=counts["live_rows"],
        broken_rate=counts["broken_rate"],
        out=str(OUT_CSV),
    )
    print(f"live_rows={counts['live_rows']} broken_rate={counts['broken_rate']} " f"-> {OUT_CSV}")
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", type=str, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    asyncio.run(extract(business_id=args.business_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
