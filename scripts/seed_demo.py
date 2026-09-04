"""Load a seeded synthetic batch into a real database.

    python scripts/seed_demo.py --batch-size 120 --seed 42

The API demo needs rows to act on, and typing them by hand produces a book that
is too small and too tidy to show anything interesting. This loads the same
generator the tests and the training pipeline use, so the invoices in a live
database are drawn from the same distribution as the ones every metric in
``docs/`` was computed on.

Two deliberate constraints:

* **Schema is Alembic's job.** This script inserts rows and creates nothing. If
  the tables are absent it says so and exits, rather than conjuring a schema
  with no migration history behind it -- the failure mode that makes the next
  ``alembic upgrade head`` impossible to apply.
* **Ground truth stays out.** ``recovered``, ``recovered_date`` and the hidden
  archetype are never written. They are properties of the simulation, not facts
  the agent could know at scoring time, and a column holding them would
  eventually be read by something.

Re-running is safe: existing customers and invoices are matched by their
business IDs and updated rather than duplicated.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, inspect, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.session import async_session_maker, engine  # noqa: E402
from app.models.enums import ContactChannel, EscalationState, InvoiceStatus  # noqa: E402
from app.models.tables import Customer, Invoice  # noqa: E402
from src.data.synthetic_generator import generate_batch  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=120, help="invoices to load")
    parser.add_argument("--customers", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="anchor date; invoices are aged relative to this",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="delete existing invoices and customers first",
    )
    parser.add_argument(
        "--email-base",
        type=str,
        default=None,
        help="Base email for Gmail plus-addressing (e.g. harish0421mw@gmail.com)",
    )
    return parser


async def _tables_exist() -> bool:
    """Whether the schema has been migrated, checked without creating anything."""

    async with engine.connect() as connection:
        names = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )
    return {"customers", "invoices"}.issubset(set(names))


def _channel(raw: str) -> ContactChannel:
    try:
        return ContactChannel(raw.lower())
    except ValueError:
        return ContactChannel.EMAIL


async def _upsert_customers(
    session: AsyncSession, generated, *, email_base: str | None = None
) -> dict[str, Customer]:
    existing = {
        customer.customer_id: customer
        for customer in (await session.scalars(select(Customer))).all()
    }

    by_business_id: dict[str, Customer] = {}
    for record in generated:
        customer = existing.get(record.customer_id) or Customer(customer_id=record.customer_id)
        customer.name = record.name
        customer.industry = record.industry
        if email_base and "@" in email_base:
            user_part, domain_part = email_base.split("@", 1)
            if "+" in user_part:
                user_part = user_part.split("+", 1)[0]
            tag = record.customer_id.lower().replace("-", "_")
            customer.email = f"{user_part}+{tag}@{domain_part}"
        else:
            customer.email = f"ap+{record.customer_id.lower()}@example.invalid"
        customer.preferred_channel = _channel(record.preferred_channel)
        customer.tenure_months = record.tenure_months
        customer.invoice_count = record.invoice_count
        customer.avg_invoice_amount = record.avg_invoice_amount
        customer.on_time_ratio_90d = record.on_time_ratio_90d
        customer.on_time_ratio_all_time = record.on_time_ratio_all_time
        customer.avg_days_late = record.avg_days_late
        customer.prior_broken_promises_count = record.prior_broken_promises_count
        customer.prior_disputes_count = record.prior_disputes_count

        session.add(customer)
        by_business_id[record.customer_id] = customer

    # Flush so every customer has its primary key before invoices reference it.
    await session.flush()
    return by_business_id


#: The generator's 0-3 escalation tier, in the ladder's own terms.
_TIER_TO_STATE = {
    0: EscalationState.MONITORING,
    1: EscalationState.REMINDED,
    2: EscalationState.ESCALATED,
    3: EscalationState.HUMAN_HANDOFF,
}


async def _upsert_invoices(
    session: AsyncSession,
    generated,
    customers: dict[str, Customer],
    *,
    as_of: date,
) -> int:
    existing = {
        invoice.invoice_id: invoice for invoice in (await session.scalars(select(Invoice))).all()
    }

    written = 0
    for record in generated:
        customer = customers.get(record.customer_id)
        if customer is None:
            continue

        invoice = existing.get(record.invoice_id) or Invoice(invoice_id=record.invoice_id)
        invoice.customer_pk = customer.id
        invoice.amount = record.amount
        invoice.currency = record.currency
        invoice.payment_terms_days = record.payment_terms_days

        # Re-anchor the generator's timeline onto --as-of, so a seeded book is
        # overdue *today* rather than on whatever date the batch was generated.
        invoice.due_date = as_of - timedelta(days=record.days_overdue_at_flag)
        invoice.issue_date = invoice.due_date - timedelta(days=record.payment_terms_days)

        invoice.status = InvoiceStatus.OPEN
        invoice.escalation_state = _TIER_TO_STATE.get(
            record.current_escalation_tier, EscalationState.MONITORING
        )
        invoice.ladder_index = record.current_escalation_tier
        invoice.prior_reminders_sent = record.prior_reminders_sent
        invoice.last_contact_at = (
            datetime.combine(
                as_of - timedelta(days=record.days_since_last_contact),
                time(9, 0),
                tzinfo=UTC,
            )
            if record.prior_reminders_sent
            else None
        )
        invoice.amount_paid = 0.0
        invoice.paid_at = None

        session.add(invoice)
        written += 1

    return written


async def seed(args: argparse.Namespace) -> int:
    if not await _tables_exist():
        print(
            "The customers/invoices tables are missing. Run migrations first:\n"
            "    alembic upgrade head",
            file=sys.stderr,
        )
        return 1

    batch = generate_batch(
        batch_size=args.batch_size,
        customer_count=args.customers,
        seed=args.seed,
    )

    async with async_session_maker() as session:
        if args.truncate:
            for record in (await session.scalars(select(Invoice))).all():
                await session.delete(record)
            for record in (await session.scalars(select(Customer))).all():
                await session.delete(record)
            await session.flush()
            print("cleared existing customers and invoices")

        customers = await _upsert_customers(session, batch.customers, email_base=args.email_base)
        written = await _upsert_invoices(session, batch.invoices, customers, as_of=args.as_of)
        await session.commit()

        total_customers = await session.scalar(select(func.count()).select_from(Customer))
        total_invoices = await session.scalar(select(func.count()).select_from(Invoice))
        outstanding = await session.scalar(select(func.sum(Invoice.amount))) or 0.0

    print(f"seeded {len(customers)} customers and {written} invoices (seed={args.seed})")
    print(f"database now holds {total_customers} customers, {total_invoices} invoices")
    print(f"total outstanding: Rs {outstanding:,.0f}")
    print("\nTry it:")
    print("    curl localhost:8000/api/v1/reports/batch")
    return 0


async def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return await seed(args)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
