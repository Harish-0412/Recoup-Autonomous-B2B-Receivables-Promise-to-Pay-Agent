"""QuickBooks Online integration: Intuit OAuth2 + overdue invoice sync.

Identity mapping
----------------
* Customer: QBO Customer.Id → ``qbo-{Id}`` as Recoup customer_id.
* Invoice: QBO Invoice.Id → stored in ``invoices.external_ids["quickbooks_id"]``.

Sync logic
----------
1. Refresh the Intuit access token (QBO tokens last 1 hour).
2. Query invoices with balance > 0 using the Intuit V3 Query API.
3. Upsert customers and invoices. Credit memos → negative allocations.

API reference: https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/invoice

Note on QBO environments
------------------------
Sandbox: https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/
Production: https://quickbooks.api.intuit.com/v3/company/{realm_id}/

The QBO_ENVIRONMENT setting controls which base URL is used.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AllocationSource
from app.services import repository
from app.services.integrations.shared import IngestResult, normalize_date, safe_float

logger = logging.getLogger(__name__)

INTUIT_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_BASE_SANDBOX = "https://sandbox-quickbooks.api.intuit.com/v3/company"
QBO_BASE_PRODUCTION = "https://quickbooks.api.intuit.com/v3/company"


class QuickBooksClient:
    """Thin async wrapper around the Intuit QuickBooks Online V3 API."""

    def __init__(
        self,
        realm_id: str,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        environment: str = "sandbox",
    ) -> None:
        self.realm_id = realm_id
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = QBO_BASE_PRODUCTION if environment == "production" else QBO_BASE_SANDBOX

    async def refresh_access_token(self) -> dict[str, Any]:
        """Exchange the refresh token for a new Intuit access token."""
        import base64

        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                INTUIT_TOKEN_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data["access_token"]
            return data

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def query(self, sql: str) -> dict[str, Any]:
        """Run a Intuit SQL-like query against the QBO company."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/{self.realm_id}/query",
                headers=self._headers(),
                params={"query": sql, "minorversion": "65"},
            )
            resp.raise_for_status()
            return resp.json()

    async def fetch_overdue_invoices(self, *, start_position: int = 1) -> dict[str, Any]:
        """Query invoices with a remaining balance, paginated."""
        sql = (
            "SELECT * FROM Invoice WHERE Balance > '0' "
            f"STARTPOSITION {start_position} MAXRESULTS 200"
        )
        return await self.query(sql)


async def sync(
    session: AsyncSession,
    business_id: str,
    *,
    realm_id: str,
    access_token: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    environment: str = "sandbox",
    lookback_days: int = 90,
) -> IngestResult:
    """Sync overdue QuickBooks invoices into Recoup for one tenant."""
    result = IngestResult(provider="quickbooks")
    client = QuickBooksClient(
        realm_id, access_token, refresh_token, client_id, client_secret, environment
    )

    try:
        token_resp = await client.refresh_access_token()
        new_expires_in = int(token_resp.get("expires_in", 3600))
    except Exception as exc:
        result.errors.append(f"Token refresh failed: {exc}")
        return result

    start_position = 1
    while True:
        try:
            data = await client.fetch_overdue_invoices(start_position=start_position)
        except Exception as exc:
            result.errors.append(f"Query at position {start_position} failed: {exc}")
            break

        query_response = data.get("QueryResponse", {})
        invoices = query_response.get("Invoice", [])
        if not invoices:
            break

        for inv_data in invoices:
            try:
                await _process_qbo_invoice(session, business_id, inv_data, result)
            except Exception as exc:
                qbo_id = inv_data.get("Id", "?")
                result.errors.append(f"Invoice {qbo_id}: {exc}")

        total_count = query_response.get("totalCount", 0)
        start_position += len(invoices)
        if start_position > total_count:
            break

    await session.flush()
    return result


async def _process_qbo_invoice(
    session: AsyncSession,
    business_id: str,
    inv_data: dict[str, Any],
    result: IngestResult,
) -> None:
    """Upsert one QBO invoice and its customer into Recoup."""
    qbo_invoice_id = str(inv_data.get("Id", ""))
    customer_ref = inv_data.get("CustomerRef", {})
    qbo_customer_id = str(customer_ref.get("value", ""))
    customer_name = customer_ref.get("name", qbo_customer_id)

    if not qbo_customer_id:
        result.errors.append(f"Invoice {qbo_invoice_id}: missing CustomerRef")
        return

    recoup_customer_id = f"qbo-{qbo_customer_id}"
    customer, created = await repository.upsert_customer(
        session,
        business_id,
        customer_id=recoup_customer_id,
        source="quickbooks",
        external_id=qbo_customer_id,
        name=customer_name,
    )
    if created:
        result.customers_created += 1
    else:
        result.customers_updated += 1

    # Date mapping
    recoup_invoice_id = f"qbo-{qbo_invoice_id}"
    issue_date = normalize_date(inv_data.get("TxnDate"))
    due_date = normalize_date(inv_data.get("DueDate")) or issue_date
    if issue_date is None:
        result.invoices_skipped += 1
        result.errors.append(f"Invoice {qbo_invoice_id}: missing TxnDate")
        return

    total = safe_float(inv_data.get("TotalAmt", 0))
    # QBO uses USD by default; the currency code is in CurrencyRef
    currency_ref = inv_data.get("CurrencyRef", {})
    currency_code = currency_ref.get("value", "USD")

    invoice, inv_created = await repository.upsert_invoice(
        session,
        business_id,
        invoice_id=recoup_invoice_id,
        customer_pk=customer.id,
        erp_source="quickbooks",
        external_id=qbo_invoice_id,
        amount=total,
        currency=currency_code,
        issue_date=issue_date,
        due_date=due_date,
        payment_terms_days=30,  # QBO doesn't always expose terms in the summary
    )
    if inv_created:
        result.invoices_created += 1
    else:
        result.invoices_updated += 1

    # Credit memos linked to this invoice via LinkedTxn
    linked = inv_data.get("LinkedTxn", [])
    for txn in linked:
        if txn.get("TxnType") != "CreditMemo":
            continue
        credit_id = str(txn.get("TxnId", ""))
        # QBO doesn't give the credit amount directly in the summary;
        # use a placeholder note. The balance field on the invoice already
        # reflects applied credits, so we note it rather than creating a
        # duplicate allocation.
        credit_amount = safe_float(inv_data.get("Balance", 0)) - total
        if credit_id and credit_amount < 0:
            alloc = await repository.create_allocation(
                session,
                business_id=business_id,
                invoice_pk=invoice.id,
                source=AllocationSource.ERP_CREDIT_NOTE,
                provider_ref=f"qbo-creditmemo-{credit_id}",
                amount=credit_amount,
                currency=currency_code,
                recorded_by="erp_sync",
                notes=f"QBO Credit Memo {credit_id}",
            )
            if alloc is not None:
                result.credit_notes_applied += 1
                await repository.recompute_and_update_invoice(session, invoice, business_id)
