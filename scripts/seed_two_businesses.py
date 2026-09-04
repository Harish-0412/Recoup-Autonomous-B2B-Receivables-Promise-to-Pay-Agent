"""Seed two businesses with overlapping keys for Wave 1 isolation checks.

    python scripts/seed_two_businesses.py [--reset]

Creates `acme` and `globex`, each with customer C-1 and invoice INV-1042, plus
per-tenant API/task keys recorded in the businesses registry. With --reset,
existing acme/globex tenant rows are removed first (default business untouched).
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, select  # noqa: E402

from app.core.logging import setup_logging  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402
from app.models import Business, Customer, Invoice, InvoiceStatus  # noqa: E402

TENANTS = ("acme", "globex")


async def seed(*, reset: bool = False) -> dict[str, dict[str, str]]:
    from app.models import (
        BatchRunRecord,
        ContactLog,
        CustomerDriftFlag,
        DecisionTrace,
        InboundReply,
        OptOut,
        Promise,
        WebhookEvent,
    )

    out: dict[str, dict[str, str]] = {}
    async with async_session_maker() as session:
        for business_id in TENANTS:
            if reset:
                for model in (
                    ContactLog,
                    Promise,
                    OptOut,
                    InboundReply,
                    DecisionTrace,
                    WebhookEvent,
                    BatchRunRecord,
                    CustomerDriftFlag,
                    Invoice,
                    Customer,
                ):
                    await session.execute(delete(model).where(model.business_id == business_id))
                await session.execute(delete(Business).where(Business.business_id == business_id))
                await session.flush()

            row = (
                await session.execute(select(Business).where(Business.business_id == business_id))
            ).scalar_one_or_none()
            if row is None:
                row = Business(
                    business_id=business_id,
                    name=business_id.title(),
                    api_key=f"test_{business_id}_operator_{secrets.token_hex(8)}",
                    task_api_key=f"test_{business_id}_cron_{secrets.token_hex(8)}",
                )
                session.add(row)
                await session.flush()
            elif not row.api_key or not row.task_api_key:
                row.api_key = row.api_key or f"test_{business_id}_operator_{secrets.token_hex(8)}"
                row.task_api_key = (
                    row.task_api_key or f"test_{business_id}_cron_{secrets.token_hex(8)}"
                )
                await session.flush()

            customer = (
                await session.execute(
                    select(Customer).where(
                        Customer.business_id == business_id,
                        Customer.customer_id == "C-1",
                    )
                )
            ).scalar_one_or_none()
            if customer is None:
                customer = Customer(
                    business_id=business_id,
                    customer_id="C-1",
                    name=f"{business_id.title()} Traders",
                    email=f"ap@{business_id}.example",
                )
                session.add(customer)
                await session.flush()

            invoice = (
                await session.execute(
                    select(Invoice).where(
                        Invoice.business_id == business_id,
                        Invoice.invoice_id == "INV-1042",
                    )
                )
            ).scalar_one_or_none()
            if invoice is None:
                today = date(2026, 9, 1)
                invoice = Invoice(
                    business_id=business_id,
                    invoice_id="INV-1042",
                    customer_pk=customer.id,
                    amount=100_000.0,
                    currency="INR",
                    issue_date=today - timedelta(days=50),
                    due_date=today - timedelta(days=20),
                    status=InvoiceStatus.OPEN,
                )
                session.add(invoice)
                await session.flush()

            out[business_id] = {
                "api_key": row.api_key or "",
                "task_api_key": row.task_api_key or "",
            }
        await session.commit()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args(argv)
    setup_logging()
    keys = asyncio.run(seed(reset=args.reset))
    for business_id, pair in keys.items():
        print(f"{business_id}: customer C-1, invoice INV-1042")
        print(f"  API_KEY={pair['api_key']}")
        print(f"  TASK_API_KEY={pair['task_api_key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
