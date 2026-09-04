"""Tally file import: CSV and XML.

Tally (TallyPrime / Tally ERP 9) has no public cloud REST API suitable for
direct integration. We support two export formats that Tally can generate:

1. **CSV** — standard Tally ledger export. Operator exports from
   Gateway of Tally → Display → Account Books → Ledger → Export to CSV.

2. **XML** — TallyPrime XML export via the local HTTP server or file export.
   The voucher-level XML schema is what Tally uses internally.

We do NOT implement a fake REST client for Tally. This module is a pure parser
that accepts a file content string and returns an IngestResult.

CSV column mapping (expected headers, case-insensitive)
-------------------------------------------------------
voucher_number  → invoice_id (prefixed with "tally-")
ledger_name     → customer (mapped to customer_id "tally-{ledger_name}")
date            → issue_date
due_date        → due_date (if present; falls back to date + 30 days)
amount          → amount (positive = receivable)
narration       → notes
currency        → currency (default INR)

XML tag mapping (TallyPrime voucher XML)
-----------------------------------------
<VOUCHERNUMBER> → invoice_id
<PARTYLEDGERNAME> → customer
<DATE> → issue_date (YYYYMMDD format)
<AMOUNT> → amount (Tally uses negative for credit; we flip sign)

Both parsers are deliberately lenient: an unrecognised row is skipped with an
error recorded in IngestResult rather than aborting the whole import.
"""

from __future__ import annotations

import csv
import io
import logging
import xml.etree.ElementTree as ET

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import repository
from app.services.integrations.shared import IngestResult, normalize_date, safe_float

logger = logging.getLogger(__name__)

#: CSV header synonyms (first match wins)
_CSV_SYNONYMS: dict[str, list[str]] = {
    "voucher_number": [
        "voucher_number",
        "voucher no",
        "voucherno",
        "invoice_no",
        "invoice no",
        "bill no",
        "billno",
    ],
    "ledger_name": ["ledger_name", "ledger name", "party name", "partyname", "customer", "debtor"],
    "date": ["date", "voucher_date", "invoice_date"],
    "due_date": ["due_date", "due date", "payment_due_date"],
    "amount": ["amount", "debit_amount", "debit amount", "outstanding", "balance"],
    "currency": ["currency", "currency_code"],
    "narration": ["narration", "remarks", "description"],
}


def _map_csv_headers(headers: list[str]) -> dict[str, int]:
    """Return a mapping of canonical field name → column index."""
    lower_headers = [h.strip().lower() for h in headers]
    result: dict[str, int] = {}
    for canonical, synonyms in _CSV_SYNONYMS.items():
        for syn in synonyms:
            if syn in lower_headers:
                result[canonical] = lower_headers.index(syn)
                break
    return result


async def import_csv(
    session: AsyncSession,
    business_id: str,
    *,
    file_content: str,
    filename: str = "tally_export.csv",
) -> IngestResult:
    """Parse a Tally CSV export and upsert customers and invoices."""
    result = IngestResult(provider="tally")
    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)

    if not rows:
        result.errors.append("CSV file is empty")
        return result

    headers = rows[0]
    col_map = _map_csv_headers(headers)

    required = {"voucher_number", "ledger_name", "date", "amount"}
    missing = required - set(col_map.keys())
    if missing:
        result.errors.append(f"Missing required columns: {', '.join(sorted(missing))}")
        return result

    for row_num, row in enumerate(rows[1:], start=2):
        if not row or all(c.strip() == "" for c in row):
            continue
        try:
            await _process_tally_row(session, business_id, row, col_map, result, row_num=row_num)
        except Exception as exc:
            result.errors.append(f"Row {row_num}: {exc}")

    await session.flush()
    return result


async def _process_tally_row(
    session: AsyncSession,
    business_id: str,
    row: list[str],
    col_map: dict[str, int],
    result: IngestResult,
    *,
    row_num: int,
) -> None:
    """Upsert one CSV row as a customer + invoice."""

    def _get(field: str, default: str = "") -> str:
        idx = col_map.get(field)
        if idx is None or idx >= len(row):
            return default
        return row[idx].strip()

    voucher_number = _get("voucher_number")
    ledger_name = _get("ledger_name")
    date_str = _get("date")
    amount_str = _get("amount")
    due_date_str = _get("due_date")
    currency = _get("currency", "INR") or "INR"
    narration = _get("narration")

    if not voucher_number or not ledger_name:
        result.invoices_skipped += 1
        return

    amount = safe_float(amount_str.replace(",", ""))
    if amount <= 0:
        # Tally may export all vouchers including credits; skip zero/negative
        result.invoices_skipped += 1
        return

    issue_date = normalize_date(date_str)
    if issue_date is None:
        result.errors.append(f"Row {row_num}: cannot parse date {date_str!r}")
        result.invoices_skipped += 1
        return

    from datetime import timedelta

    due_date = normalize_date(due_date_str) or (issue_date + timedelta(days=30))

    # Stable Recoup keys
    recoup_customer_id = f"tally-{ledger_name.lower().replace(' ', '-')[:32]}"
    recoup_invoice_id = f"tally-{voucher_number}"

    customer, created = await repository.upsert_customer(
        session,
        business_id,
        customer_id=recoup_customer_id,
        source="tally",
        external_id=ledger_name,
        name=ledger_name,
    )
    if created:
        result.customers_created += 1
    else:
        result.customers_updated += 1

    invoice, inv_created = await repository.upsert_invoice(
        session,
        business_id,
        invoice_id=recoup_invoice_id,
        customer_pk=customer.id,
        erp_source="tally",
        external_id=voucher_number,
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


async def import_xml(
    session: AsyncSession,
    business_id: str,
    *,
    file_content: str,
    filename: str = "tally_export.xml",
) -> IngestResult:
    """Parse a TallyPrime XML export and upsert customers and invoices.

    Supports both single-voucher XML and the multi-VOUCHER envelope that
    TallyPrime produces when exporting a date range.
    """
    result = IngestResult(provider="tally")

    try:
        root = ET.fromstring(file_content)
    except ET.ParseError as exc:
        result.errors.append(f"XML parse error: {exc}")
        return result

    # Find all VOUCHER elements wherever they appear in the tree
    vouchers = root.findall(".//VOUCHER")
    if not vouchers:
        # Some Tally versions wrap under TALLYMESSAGE > VOUCHER
        vouchers = root.findall(".//TALLYMESSAGE/VOUCHER")
    if not vouchers:
        result.errors.append("No VOUCHER elements found in XML")
        return result

    for idx, voucher in enumerate(vouchers, start=1):
        try:
            await _process_tally_voucher(session, business_id, voucher, result)
        except Exception as exc:
            result.errors.append(f"Voucher {idx}: {exc}")

    await session.flush()
    return result


def _xml_text(element: ET.Element | None, tag: str, default: str = "") -> str:
    if element is None:
        return default
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else default


async def _process_tally_voucher(
    session: AsyncSession,
    business_id: str,
    voucher: ET.Element,
    result: IngestResult,
) -> None:
    """Upsert one TallyPrime VOUCHER element."""
    voucher_number = _xml_text(voucher, "VOUCHERNUMBER")
    ledger_name = _xml_text(voucher, "PARTYLEDGERNAME")
    date_str = _xml_text(voucher, "DATE")
    voucher_type = _xml_text(voucher, "VOUCHERTYPENAME", "").lower()

    # Only process receivable voucher types (sales invoices)
    if voucher_type and voucher_type not in ("sales", "receipt", "journal", ""):
        result.invoices_skipped += 1
        return

    if not voucher_number or not ledger_name:
        result.invoices_skipped += 1
        return

    # Tally DATE format is YYYYMMDD
    from datetime import datetime, timedelta

    issue_date = None
    try:
        issue_date = datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        issue_date = normalize_date(date_str)

    if issue_date is None:
        result.errors.append(f"Voucher {voucher_number}: cannot parse date {date_str!r}")
        result.invoices_skipped += 1
        return

    due_date = issue_date + timedelta(days=30)

    # Amount: Tally uses negative for credit in AMOUNT; we want positive
    amount_str = _xml_text(voucher, "AMOUNT")
    amount = abs(safe_float(amount_str.replace(",", "")))
    if amount <= 0:
        result.invoices_skipped += 1
        return

    currency = _xml_text(voucher, "CURRENCYNAME", "INR") or "INR"

    recoup_customer_id = f"tally-{ledger_name.lower().replace(' ', '-')[:32]}"
    recoup_invoice_id = f"tally-{voucher_number}"

    customer, created = await repository.upsert_customer(
        session,
        business_id,
        customer_id=recoup_customer_id,
        source="tally",
        external_id=ledger_name,
        name=ledger_name,
    )
    if created:
        result.customers_created += 1
    else:
        result.customers_updated += 1

    invoice, inv_created = await repository.upsert_invoice(
        session,
        business_id,
        invoice_id=recoup_invoice_id,
        customer_pk=customer.id,
        erp_source="tally",
        external_id=voucher_number,
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
