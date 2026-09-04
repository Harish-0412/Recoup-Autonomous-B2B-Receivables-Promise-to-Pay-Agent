"""Safely rewrite seeded customer emails to Gmail plus-addresses.

    python scripts/update_customer_emails.py --base-email harish0421mw@gmail.com

Why this exists:
In live mode (DRY_RUN=false), Recoup delivers actual emails to whatever
addresses are stored in the database. The seeded synthetic data contains
synthetic domains (`example.invalid`). If DRY_RUN is turned off without
updating those addresses, emails fail to deliver.

Gmail supports plus-addressing (also called sub-addressing):
    yourname+anytag@gmail.com -> delivered directly to yourname@gmail.com

This script rewrites all customers in the database to:
    {user}+{customer_id.lower()}@{domain}

For example:
    harish0421mw+cus_0001@gmail.com
    harish0421mw+cus_0002@gmail.com

All 40 conversations can be tracked in your single Gmail inbox, while
preventing any emails from being sent to external strangers.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402
from app.models.tables import Customer  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-email",
        type=str,
        default="harish0421mw@gmail.com",
        help="Base email address to derive plus-addresses from (e.g. you@gmail.com)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without updating the database",
    )
    return parser


async def rewrite_customer_emails(base_email: str, dry_run: bool = False) -> None:
    if "@" not in base_email:
        print(f"Error: invalid email address: '{base_email}'")
        sys.exit(1)

    user_part, domain_part = base_email.split("@", 1)
    # Strip any existing plus tag from the base address
    if "+" in user_part:
        user_part = user_part.split("+", 1)[0]

    async with async_session_maker() as session:
        customers = (await session.scalars(select(Customer))).all()
        if not customers:
            print("No customers found in database. Run `python scripts/seed_demo.py` first.")
            return

        print(f"\nFound {len(customers)} customers in database.")
        print(f"Target pattern: {user_part}+<customer_id>@{domain_part}\n")

        updates: list[tuple[str, str, str]] = []
        for customer in customers:
            tag = customer.customer_id.lower().replace("-", "_")
            new_email = f"{user_part}+{tag}@{domain_part}"
            updates.append((customer.customer_id, customer.email or "<empty>", new_email))
            if not dry_run:
                customer.email = new_email

        # Print preview of first 5
        print("Sample email rewrites:")
        for cid, old_e, new_e in updates[:5]:
            print(f"  [{cid}]: {old_e} -> {new_e}")

        if len(updates) > 5:
            print(f"  ... and {len(updates) - 5} more.")

        if dry_run:
            print("\n[DRY-RUN] No changes were written to the database.")
        else:
            await session.commit()
            print(f"\nSuccessfully updated {len(customers)} customer emails in the database!")
            print("You can now safely test with DRY_RUN=false using your inbox.")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(rewrite_customer_emails(args.base_email, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
