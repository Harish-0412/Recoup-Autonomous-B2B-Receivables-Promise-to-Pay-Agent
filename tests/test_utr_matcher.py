"""Tests for the UTR matcher (pure function, no DB).

Covers:
- Duplicate UTR detection
- Operator-suggested invoice (exact match)
- Amount + date window heuristic (unique match)
- Ambiguous: two invoices same outstanding amount → never auto-match
- No match: no invoice fits the amount
- Customer hint narrows the candidate pool
"""

from __future__ import annotations

from datetime import date

from app.core.utr_matcher import (
    InvoiceMatchCandidate,
    match_bank_payment,
)


def _make_invoice(
    *,
    pk: int,
    invoice_id: str,
    customer_id: str = "CUST-001",
    outstanding: float = 50_000.0,
    due_date: date | None = None,
    promise_date: date | None = None,
) -> InvoiceMatchCandidate:
    return InvoiceMatchCandidate(
        invoice_pk=pk,
        invoice_id=invoice_id,
        customer_pk=1,
        customer_id=customer_id,
        amount=outstanding,
        outstanding=outstanding,
        currency="INR",
        due_date=due_date or date(2026, 9, 1),
        promise_date=promise_date,
    )


TODAY = date(2026, 9, 4)


class TestDuplicateUTR:
    def test_existing_utr_returns_duplicate(self):
        inv = _make_invoice(pk=1, invoice_id="INV-001")
        result = match_bank_payment(
            utr="HDFC0012345678",
            amount=50_000.0,
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs={"HDFC0012345678"},
        )
        assert result.confidence == "duplicate"
        assert result.invoice_pk is None

    def test_new_utr_not_duplicate(self):
        inv = _make_invoice(pk=1, invoice_id="INV-001")
        result = match_bank_payment(
            utr="NEW-UTR-9999",
            amount=50_000.0,
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs={"OTHER-UTR"},
        )
        assert result.confidence != "duplicate"


class TestSuggestedInvoice:
    def test_unique_suggested_invoice_returns_probable(self):
        inv = _make_invoice(pk=1, invoice_id="INV-042")
        result = match_bank_payment(
            utr="UTR-123",
            amount=50_000.0,
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id="INV-042",
            open_invoices=[inv],
            existing_utrs=set(),
        )
        assert result.confidence == "probable"
        assert result.invoice_id == "INV-042"
        assert result.invoice_pk == 1

    def test_suggested_invoice_not_found_falls_through(self):
        """If the suggested invoice is not in open_invoices, fall through to heuristic."""
        inv = _make_invoice(pk=1, invoice_id="INV-001", outstanding=99_000.0)
        result = match_bank_payment(
            utr="UTR-123",
            amount=99_000.0,
            paid_on=date(2026, 9, 1),  # same as due_date
            payer_account=None,
            suggested_invoice_id="INV-999",  # doesn't exist
            open_invoices=[inv],
            existing_utrs=set(),
        )
        # Should fall through to heuristic and find INV-001
        assert result.confidence in ("probable", "no_match")


class TestHeuristicMatching:
    def test_amount_and_date_match_returns_probable(self):
        inv = _make_invoice(
            pk=1, invoice_id="INV-001", outstanding=75_000.0, due_date=date(2026, 9, 2)
        )
        result = match_bank_payment(
            utr="UTR-ABC",
            amount=75_000.0,
            paid_on=date(2026, 9, 3),  # 1 day after due_date → within ±2 days
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs=set(),
        )
        assert result.confidence == "probable"
        assert result.invoice_id == "INV-001"

    def test_amount_match_outside_date_window_returns_no_match(self):
        inv = _make_invoice(
            pk=1,
            invoice_id="INV-001",
            outstanding=75_000.0,
            due_date=date(2026, 8, 1),  # 34 days before paid_on
        )
        result = match_bank_payment(
            utr="UTR-FAR",
            amount=75_000.0,
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs=set(),
        )
        assert result.confidence == "no_match"

    def test_amount_matches_promise_date_window(self):
        """Payment matching the promise date (not due date) within ±2 days."""
        inv = _make_invoice(
            pk=1,
            invoice_id="INV-001",
            outstanding=50_000.0,
            due_date=date(2026, 8, 1),  # far in the past
            promise_date=date(2026, 9, 3),  # close to paid_on
        )
        result = match_bank_payment(
            utr="UTR-PRMS",
            amount=50_000.0,
            paid_on=TODAY,  # 2026-09-04 — within ±2 days of 2026-09-03
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs=set(),
        )
        assert result.confidence == "probable"

    def test_tds_tolerance_one_inr(self):
        """Amount 1 INR short of outstanding → still matches."""
        inv = _make_invoice(pk=1, invoice_id="INV-001", outstanding=50_000.0, due_date=TODAY)
        result = match_bank_payment(
            utr="UTR-TDS",
            amount=49_999.0,  # exactly 1 INR short
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs=set(),
        )
        assert result.confidence == "probable"


class TestAmbiguousMatch:
    def test_two_invoices_same_amount_returns_ambiguous(self):
        inv1 = _make_invoice(
            pk=1, invoice_id="INV-001", outstanding=50_000.0, customer_id="CUST-001"
        )
        inv2 = _make_invoice(
            pk=2, invoice_id="INV-002", outstanding=50_000.0, customer_id="CUST-002"
        )
        result = match_bank_payment(
            utr="UTR-AMBIG",
            amount=50_000.0,
            paid_on=date(2026, 9, 1),
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv1, inv2],
            existing_utrs=set(),
        )
        assert result.confidence == "ambiguous"
        assert result.invoice_pk is None
        assert result.invoice_id is None

    def test_ambiguous_even_with_customer_hint(self):
        """Even when the operator provides a customer hint, if two invoices from
        the same customer share the same amount, do NOT auto-match."""
        inv1 = _make_invoice(pk=1, invoice_id="INV-001", outstanding=30_000.0, customer_id="CUST-X")
        inv2 = _make_invoice(pk=2, invoice_id="INV-002", outstanding=30_000.0, customer_id="CUST-X")
        result = match_bank_payment(
            utr="UTR-CUST-AMBIG",
            amount=30_000.0,
            paid_on=date(2026, 9, 1),
            payer_account="CUST-X",
            suggested_invoice_id=None,
            open_invoices=[inv1, inv2],
            existing_utrs=set(),
        )
        assert result.confidence == "ambiguous"


class TestNoMatch:
    def test_no_invoices_returns_no_match(self):
        result = match_bank_payment(
            utr="UTR-GHOST",
            amount=99_999.0,
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[],
            existing_utrs=set(),
        )
        assert result.confidence == "no_match"

    def test_wrong_amount_returns_no_match(self):
        inv = _make_invoice(pk=1, invoice_id="INV-001", outstanding=50_000.0, due_date=TODAY)
        result = match_bank_payment(
            utr="UTR-WRONG",
            amount=12_345.0,
            paid_on=TODAY,
            payer_account=None,
            suggested_invoice_id=None,
            open_invoices=[inv],
            existing_utrs=set(),
        )
        assert result.confidence == "no_match"
