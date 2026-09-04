"""Shared types for ERP integration services.

Every provider returns an IngestResult summarising what was created, updated,
and skipped in one sync run. Errors are collected rather than raised so a bad
invoice does not abort the entire sync -- one malformed Zoho invoice should not
prevent the other 200 from landing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class IngestResult:
    """Aggregate outcome of one ERP sync or file import."""

    provider: str = ""
    customers_created: int = 0
    customers_updated: int = 0
    invoices_created: int = 0
    invoices_updated: int = 0
    invoices_skipped: int = 0
    credit_notes_applied: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: IngestResult) -> IngestResult:
        """Accumulate another result into this one (used by multi-page syncs)."""
        self.customers_created += other.customers_created
        self.customers_updated += other.customers_updated
        self.invoices_created += other.invoices_created
        self.invoices_updated += other.invoices_updated
        self.invoices_skipped += other.invoices_skipped
        self.credit_notes_applied += other.credit_notes_applied
        self.errors.extend(other.errors)
        return self


def normalize_date(value: Any) -> date | None:
    """Parse a date string in common ERP formats, or return None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            from datetime import datetime as _dt

            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce a value to float, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
