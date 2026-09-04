"""Razorpay Invoices API integration.

Recoup already handles Razorpay Payment Links. This module adds a separate
ingest path for **Razorpay Invoices** (the separate Invoices product), which
some businesses use to issue invoices to their customers via Razorpay directly.

Identity mapping
----------------
* ``notes.invoice_id`` or ``receipt`` field → Recoup invoice_id.
  The executor already stamps ``notes.invoice_id`` on payment links it creates,
  so this acts as the reverse mapping.
* Customer email → customer_id via existing ``get_customer_by_email`` lookup;
  falls back to ``razorpay-{customer_id}`` if no match.
* Razorpay Invoice.id → stored in ``invoices.external_ids["razorpay_invoice_id"]``.

What we sync
------------
Only invoices in status ``issued`` or ``draft`` (i.e. not yet paid). We do NOT
re-ingest ``paid`` Razorpay invoices because those are already handled by the
payment webhook path; ingesting them would create duplicate amount_paid entries.

API reference: https://razorpay.com/docs/api/payments/invoices/
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import repository
from app.services.integrations.shared import IngestResult, safe_float

logger = logging.getLogger(__name__)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


async def sync(
    session: AsyncSession,
    business_id: str,
    *,
    key_id: str,
    key_secret: str,
    lookback_days: int = 90,
) -> IngestResult:
    """Sync unpaid Razorpay Invoices into Recoup for one tenant.

    Uses HTTP Basic Auth (key_id:key_secret) — same credentials as Payment Links.
    """
    result = IngestResult(provider="razorpay_invoices")
    auth = (key_id, key_secret)

    skip = 0
    count = 100
    while True:
        try:
            data = await _fetch_invoices(auth, skip=skip, count=count)
        except Exception as exc:
            result.errors.append(f"Fetch at skip={skip} failed: {exc}")
            break

        items = data.get("items", [])
        if not items:
            break

        for inv_data in items:
            try:
                await _process_razorpay_invoice(session, business_id, inv_data, result)
            except Exception as exc:
                rzp_id = inv_data.get("id", "?")
                result.errors.append(f"Razorpay invoice {rzp_id}: {exc}")

        skip += len(items)
        if data.get("count", 0) < count:
            break

    await session.flush()
    return result


async def _fetch_invoices(auth: tuple, *, skip: int, count: int) -> dict[str, Any]:
    """GET /v1/invoices?type=invoice&status=issued (paginated)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{RAZORPAY_API_BASE}/invoices",
            auth=auth,
            params={
                "type": "invoice",
                "count": count,
                "skip": skip,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _process_razorpay_invoice(
    session: AsyncSession,
    business_id: str,
    inv_data: dict[str, Any],
    result: IngestResult,
) -> None:
    """Upsert one Razorpay invoice and its customer into Recoup."""
    rzp_invoice_id = inv_data.get("id", "")
    rzp_status = inv_data.get("status", "")

    # Skip paid invoices — payment webhook already handles them
    if rzp_status == "paid":
        result.invoices_skipped += 1
        return

    # Derive Recoup invoice_id from notes.invoice_id or receipt
    notes = inv_data.get("notes") or {}
    recoup_invoice_id = (
        notes.get("invoice_id") or inv_data.get("receipt") or f"rzp-{rzp_invoice_id}"
    )

    # Customer resolution
    customer_email = inv_data.get("customer_details", {}).get("email", "")
    rzp_customer_id = inv_data.get("customer_id", "")
    recoup_customer_id = None

    if customer_email:
        existing_customer = await repository.get_customer_by_email(
            session, customer_email, business_id
        )
        if existing_customer is not None:
            recoup_customer_id = existing_customer.customer_id

    if recoup_customer_id is None:
        recoup_customer_id = (
            f"razorpay-{rzp_customer_id}" if rzp_customer_id else f"razorpay-{rzp_invoice_id}"
        )

    customer_name = inv_data.get("customer_details", {}).get("name", recoup_customer_id)

    customer, created = await repository.upsert_customer(
        session,
        business_id,
        customer_id=recoup_customer_id,
        source="razorpay_invoices",
        external_id=rzp_customer_id or rzp_invoice_id,
        name=customer_name,
        email=customer_email,
    )
    if created:
        result.customers_created += 1
    else:
        result.customers_updated += 1

    # Date fields: Razorpay uses Unix timestamps (seconds)
    import datetime as _dt
    from datetime import date

    def _ts_to_date(ts: Any) -> date | None:
        if not ts:
            return None
        try:
            return _dt.datetime.fromtimestamp(int(ts), tz=_dt.UTC).date()
        except (ValueError, OSError):
            return None

    issue_date = _ts_to_date(inv_data.get("date")) or _dt.date.today()
    due_date = _ts_to_date(inv_data.get("due_date")) or issue_date

    # Razorpay amounts in paise
    amount_paise = safe_float(inv_data.get("amount", 0))
    amount = amount_paise / 100.0
    currency = inv_data.get("currency", "INR")

    invoice, inv_created = await repository.upsert_invoice(
        session,
        business_id,
        invoice_id=recoup_invoice_id,
        customer_pk=customer.id,
        erp_source="razorpay_invoices",
        external_id=rzp_invoice_id,
        amount=amount,
        currency=currency,
        issue_date=issue_date,
        due_date=due_date,
        payment_terms_days=30,
    )
    if inv_created:
        result.invoices_created += 1
    else:
        result.invoices_updated += 1
