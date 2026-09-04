"""UTR / bank-transfer matching: pure function, no DB dependency.

Indian B2B payments frequently arrive via NEFT, RTGS, or UPI with a UTR
(Unique Transaction Reference) that the operator pastes from their bank
statement. This module decides, given a set of open invoices, which one
this payment most likely belongs to.

Three-tier resolution
---------------------
1. **Exact UTR** — already exists in payment_allocations? → reject as duplicate.
2. **Suggested invoice** — operator named an invoice_id *and* it is uniquely open
   for this tenant → ``probable``.
3. **Amount + customer + date** — payer_account hint resolves to a customer, and
   exactly one of that customer's open invoices matches the amount within the
   ±2-day promise/due-date window → ``probable``.
4. **Ambiguous** — two or more open invoices share the same amount (including
   across customers). Auto-matching here would be wrong half the time → queue
   for human review.
5. **No match** → ``no_match``, queue for review.

Safety invariants
-----------------
* Never auto-match across tenants. All candidates passed in must be from the
  same business_id; the repository layer enforces this before calling here.
* Never auto-match when two open invoices share the same amount, even if the
  payer_account resolves to one customer's other invoice -- the operator must
  confirm.
* A ``probable`` result is a *suggestion*, not a decision. The API records the
  allocation as unmatched (invoice_pk=NULL) and returns the suggestion;
  the human confirms via POST /payments/{id}/allocate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

_MatchConfidence = Literal["duplicate", "probable", "ambiguous", "no_match"]

#: Window around the invoice due date (or promise date) within which a payment
#: date is considered "matching". Indian bank value dates commonly differ from
#: the payment initiation date by 1-2 days.
DATE_WINDOW_DAYS: int = 2


@dataclass(frozen=True)
class InvoiceMatchCandidate:
    """A slim view of an open invoice, suitable for matching.

    All amounts are in INR (or the invoice currency). The caller must ensure
    that only invoices from the same business_id are passed in.
    """

    invoice_pk: int
    invoice_id: str
    customer_pk: int
    customer_id: str
    amount: float
    outstanding: float  # amount - amount_paid
    currency: str
    due_date: date
    promise_date: date | None  # most recent pending promise, if any


@dataclass(frozen=True)
class MatchResult:
    """The matcher's verdict for one bank payment."""

    confidence: _MatchConfidence
    invoice_pk: int | None
    invoice_id: str | None
    reason: str


def match_bank_payment(
    *,
    utr: str,
    amount: float,
    paid_on: date,
    payer_account: str | None,
    suggested_invoice_id: str | None,
    open_invoices: list[InvoiceMatchCandidate],
    existing_utrs: set[str],
) -> MatchResult:
    """Return the best match for a bank payment, or explain why none was found.

    Parameters
    ----------
    utr:
        The UTR string from the bank statement. Must be non-empty.
    amount:
        Payment amount in INR (or the invoice currency).
    paid_on:
        Bank value date of the payment.
    payer_account:
        Optional account number or ERP customer_id that the bank statement
        names as the sender. Used as a customer hint.
    suggested_invoice_id:
        The operator's explicit guess at which invoice this belongs to.
    open_invoices:
        All open (unpaid) invoices for this tenant. Must be from one tenant.
    existing_utrs:
        Set of UTR strings already present in payment_allocations for this
        tenant. Passed in rather than queried here to keep the function pure.
    """
    # -- 1. Duplicate check -------------------------------------------------
    if utr in existing_utrs:
        return MatchResult(
            confidence="duplicate",
            invoice_pk=None,
            invoice_id=None,
            reason=f"UTR {utr!r} already exists in payment_allocations for this tenant.",
        )

    # -- 2. Operator-suggested invoice --------------------------------------
    if suggested_invoice_id:
        candidates = [inv for inv in open_invoices if inv.invoice_id == suggested_invoice_id]
        if len(candidates) == 1:
            inv = candidates[0]
            return MatchResult(
                confidence="probable",
                invoice_pk=inv.invoice_pk,
                invoice_id=inv.invoice_id,
                reason=(
                    f"Operator suggested invoice {suggested_invoice_id!r}; "
                    f"it is uniquely open with outstanding {inv.outstanding:,.2f} {inv.currency}."
                ),
            )
        if len(candidates) == 0:
            # Suggested invoice not found or already paid — fall through to
            # heuristic matching rather than failing outright.
            pass

    # -- 3. Heuristic: amount + customer hint + date window -----------------
    # Resolve a customer from the payer_account hint (ERP customer_id or a
    # partial name match). Only exact customer_id matches are used here;
    # fuzzy name matching would introduce false positives.
    hint_customer_pk: int | None = None
    if payer_account:
        matched_customers = [
            inv.customer_pk
            for inv in open_invoices
            if inv.customer_id.lower() == payer_account.strip().lower()
        ]
        if matched_customers:
            hint_customer_pk = matched_customers[0]

    def _in_date_window(inv: InvoiceMatchCandidate) -> bool:
        """True if the payment date falls within ±DATE_WINDOW_DAYS of the
        invoice due date OR the promise date."""
        window = timedelta(days=DATE_WINDOW_DAYS)
        if abs((paid_on - inv.due_date).days) <= DATE_WINDOW_DAYS:
            return True
        if (
            inv.promise_date is not None
            and abs((paid_on - inv.promise_date).days) <= DATE_WINDOW_DAYS
        ):
            return True
        return False

    # Amount match: within 1 INR tolerance (TDS rounding)
    AMOUNT_TOLERANCE = 1.0

    def _amount_matches(inv: InvoiceMatchCandidate) -> bool:
        return abs(inv.outstanding - amount) <= AMOUNT_TOLERANCE

    # -- 4. Ambiguous: multiple invoices share the same outstanding amount ---
    # Check this BEFORE restricting to a single customer, because the rule is:
    # never auto-match when two open invoices share the same amount, period.
    amount_matches_globally = [inv for inv in open_invoices if _amount_matches(inv)]
    if len(amount_matches_globally) > 1:
        ids = ", ".join(inv.invoice_id for inv in amount_matches_globally[:5])
        return MatchResult(
            confidence="ambiguous",
            invoice_pk=None,
            invoice_id=None,
            reason=(
                f"{len(amount_matches_globally)} open invoices share the outstanding amount "
                f"{amount:,.2f} INR (e.g. {ids}). Human confirmation required."
            ),
        )

    # Narrow to the customer if we have a hint, then apply date window
    if hint_customer_pk is not None:
        customer_invoices = [inv for inv in open_invoices if inv.customer_pk == hint_customer_pk]
    else:
        customer_invoices = open_invoices

    date_and_amount = [
        inv for inv in customer_invoices if _amount_matches(inv) and _in_date_window(inv)
    ]

    if len(date_and_amount) == 1:
        inv = date_and_amount[0]
        customer_hint_note = (
            f" (payer_account matched customer {inv.customer_id!r})" if hint_customer_pk else ""
        )
        return MatchResult(
            confidence="probable",
            invoice_pk=inv.invoice_pk,
            invoice_id=inv.invoice_id,
            reason=(
                f"Amount {amount:,.2f} INR matches outstanding {inv.outstanding:,.2f} INR "
                f"on invoice {inv.invoice_id!r} within ±{DATE_WINDOW_DAYS}-day window"
                f"{customer_hint_note}."
            ),
        )

    # -- 5. No match --------------------------------------------------------
    return MatchResult(
        confidence="no_match",
        invoice_pk=None,
        invoice_id=None,
        reason=(
            f"No open invoice found matching amount {amount:,.2f} INR "
            f"with paid_on={paid_on.isoformat()}"
            + (f" for customer hint {payer_account!r}" if payer_account else "")
            + ". Queued for manual review."
        ),
    )
