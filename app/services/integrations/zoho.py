"""Zoho Books integration: OAuth2 token refresh + overdue invoice sync.

Identity mapping
----------------
* Customer: Zoho contact_id → customer_id prefixed with ``zoho-``; email →
  secondary lookup.
* Invoice: Zoho invoice_id → stored in ``invoices.external_ids["zoho_books_id"]``.

Sync logic
----------
1. Refresh the access token using the stored refresh_token (Zoho access tokens
   last 1 hour; the cron fires every hour so we refresh proactively).
2. Fetch overdue invoices via GET /invoices?status=overdue&per_page=200 (paginated).
3. For each invoice:
   - Upsert the customer (never overwrite Recoup's collections state).
   - Upsert the invoice (never overwrite escalation_state, ladder_index, amount_paid).
   - If the Zoho invoice has credit notes applied, create negative PaymentAllocation
     rows keyed by (business_id, erp_credit_note, zoho_creditnote_id).

The sync is advisory-locked per business to prevent concurrent runs from the
hourly cron.

API reference: https://www.zoho.com/books/api/v3/
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AllocationSource
from app.services import repository
from app.services.integrations.shared import IngestResult, normalize_date, safe_float
from src.ml.versioning import utc_now

logger = logging.getLogger(__name__)

#: Zoho Books API base URL (India region). Adjust for other regions.
ZOHO_BOOKS_BASE = "https://www.zohoapis.in/books/v3"
ZOHO_TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"


class ZohoClient:
    """Thin async wrapper around the Zoho Books REST API."""

    def __init__(
        self,
        org_id: str,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.org_id = org_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret

    async def refresh_access_token(self) -> dict[str, Any]:
        """Exchange the refresh token for a new access token.

        Returns the raw token response so the caller can update the
        integration_credential row.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                ZOHO_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                },
            )
            resp.raise_for_status()
            token_data = resp.json()
            self.access_token = token_data["access_token"]
            return token_data

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Zoho-oauthtoken {self.access_token}",
            "Content-Type": "application/json",
        }

    async def fetch_overdue_invoices(self, *, page: int = 1) -> dict[str, Any]:
        """GET /invoices?status=overdue from Zoho Books (one page)."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{ZOHO_BOOKS_BASE}/invoices",
                headers=self._headers(),
                params={
                    "organization_id": self.org_id,
                    "status": "overdue",
                    "per_page": 200,
                    "page": page,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def fetch_invoice_detail(self, zoho_invoice_id: str) -> dict[str, Any]:
        """GET one invoice detail including applied credits."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{ZOHO_BOOKS_BASE}/invoices/{zoho_invoice_id}",
                headers=self._headers(),
                params={"organization_id": self.org_id},
            )
            resp.raise_for_status()
            return resp.json()


async def sync(
    session: AsyncSession,
    business_id: str,
    *,
    org_id: str,
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    lookback_days: int = 90,
) -> IngestResult:
    """Sync overdue Zoho Books invoices into Recoup for one tenant.

    Idempotent: upsert_customer and upsert_invoice are idempotent on their
    business keys. Credit notes become negative PaymentAllocation rows keyed
    by (business_id, erp_credit_note, zoho_creditnote_id).
    """
    result = IngestResult(provider="zoho_books")
    client = ZohoClient(org_id, access_token, refresh_token, client_id, client_secret)

    # Proactively refresh the token (Zoho tokens last 1 hour)
    try:
        token_resp = await client.refresh_access_token()
        new_access_token = token_resp.get("access_token", access_token)
        new_expires_at = utc_now() + timedelta(seconds=int(token_resp.get("expires_in", 3600)))
    except Exception as exc:
        result.errors.append(f"Token refresh failed: {exc}")
        return result

    # Paginate through overdue invoices
    page = 1
    while True:
        try:
            data = await client.fetch_overdue_invoices(page=page)
        except Exception as exc:
            result.errors.append(f"Page {page} fetch failed: {exc}")
            break

        invoices = data.get("invoices", [])
        if not invoices:
            break

        for inv_data in invoices:
            try:
                await _process_zoho_invoice(session, business_id, client, inv_data, result)
            except Exception as exc:
                zoho_id = inv_data.get("invoice_id", "?")
                result.errors.append(f"Invoice {zoho_id}: {exc}")

        page_context = data.get("page_context", {})
        if not page_context.get("has_more_page", False):
            break
        page += 1

    await session.flush()
    return result


async def _process_zoho_invoice(
    session: AsyncSession,
    business_id: str,
    client: ZohoClient,
    inv_data: dict[str, Any],
    result: IngestResult,
) -> None:
    """Upsert one Zoho invoice and its customer into Recoup."""
    zoho_invoice_id = inv_data.get("invoice_id", "")
    zoho_customer_id = inv_data.get("customer_id", "")
    customer_name = inv_data.get("customer_name", zoho_customer_id)
    customer_email = inv_data.get("email") or inv_data.get("contact_persons", [{}])[0].get("email")

    # Derive a stable Recoup customer_id from the Zoho contact id
    recoup_customer_id = f"zoho-{zoho_customer_id}" if zoho_customer_id else None
    if not recoup_customer_id:
        result.errors.append(f"Invoice {zoho_invoice_id}: missing customer_id")
        return

    customer, created = await repository.upsert_customer(
        session,
        business_id,
        customer_id=recoup_customer_id,
        source="zoho_books",
        external_id=zoho_customer_id,
        name=customer_name,
        email=customer_email or "",
    )
    if created:
        result.customers_created += 1
    else:
        result.customers_updated += 1

    # Map Zoho invoice fields to Recoup invoice fields
    recoup_invoice_id = f"zoho-{zoho_invoice_id}"
    issue_date = normalize_date(inv_data.get("date"))
    due_date = normalize_date(inv_data.get("due_date"))
    if issue_date is None or due_date is None:
        result.invoices_skipped += 1
        result.errors.append(f"Invoice {zoho_invoice_id}: missing date fields")
        return

    total = safe_float(inv_data.get("total", 0))
    currency_code = inv_data.get("currency_code", "INR")

    invoice, inv_created = await repository.upsert_invoice(
        session,
        business_id,
        invoice_id=recoup_invoice_id,
        customer_pk=customer.id,
        erp_source="zoho_books",
        external_id=zoho_invoice_id,
        amount=total,
        currency=currency_code,
        issue_date=issue_date,
        due_date=due_date,
        payment_terms_days=int(inv_data.get("payment_terms", 30) or 30),
    )
    if inv_created:
        result.invoices_created += 1
    else:
        result.invoices_updated += 1

    # Credit notes → negative allocations (ERP-issued offsets)
    credits_applied = inv_data.get("credits_applied", [])
    for credit in credits_applied:
        credit_id = credit.get("creditnote_id") or credit.get("creditnoteid", "")
        credit_amount = safe_float(credit.get("amount_applied", 0))
        if not credit_id or credit_amount == 0:
            continue
        alloc = await repository.create_allocation(
            session,
            business_id=business_id,
            invoice_pk=invoice.id,
            source=AllocationSource.ERP_CREDIT_NOTE,
            provider_ref=f"zoho-{credit_id}",
            amount=-abs(credit_amount),  # always negative
            currency=currency_code,
            recorded_by="erp_sync",
            notes=f"Zoho credit note {credit_id}",
        )
        if alloc is not None:
            result.credit_notes_applied += 1
        # Recompute after credit note
        await repository.recompute_and_update_invoice(session, invoice, business_id)
