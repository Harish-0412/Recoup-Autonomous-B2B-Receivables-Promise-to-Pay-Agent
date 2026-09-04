"""Payment allocation helpers: recomputing invoice totals from the ledger.

These are pure functions with no database dependency. They take whatever
data has already been fetched and return a new value. Keeping them pure
means they can be unit-tested without an async session, and called from
both the webhook handler and the payments API without duplicating logic.

Key rule enforced here:
    invoices.amount_paid = SUM(payment_allocations.amount)

Never += on the invoice row. Two separate event types (payment.captured and
payment_link.paid) can describe the same Razorpay transaction. When they are
both written to payment_allocations with their distinct source values and the
same provider_ref, the unique constraint on (business_id, source, provider_ref)
makes one of them a DB-level no-op. This recompute then sees the correct total
because it sums what is actually in the ledger.
"""

from __future__ import annotations

from app.models.enums import InvoiceStatus

#: Promise is considered kept if the payment covers the promised amount minus
#: this tolerance. Accounts for rounding, TDS deductions, and common Indian
#: bank behaviour of settling 1 paisa short.
PROMISE_KEPT_TOLERANCE_INR: float = 1.0


def recompute_amount_paid(allocation_amounts: list[float]) -> float:
    """Sum all allocation amounts for one invoice.

    The list should include every allocation row for the invoice regardless
    of source. Negative amounts (credit notes) reduce the total automatically.
    """
    return sum(allocation_amounts)


def derive_status(
    invoice_amount: float,
    amount_paid: float,
    current_status: InvoiceStatus,
    *,
    contacted: bool = True,
) -> InvoiceStatus:
    """Return the status an invoice should have given its payment state.

    Rules (in priority order):

    1. Fully settled (amount_paid + 0.01 >= invoice_amount) → PAID.
       The 0.01 tolerance handles floating-point and rounding.
    2. Already terminal (DISPUTED, WRITTEN_OFF, HANDED_OFF) → unchanged.
       Money arriving on a disputed invoice does not un-dispute it.
    3. PAID but payment reversed / partial → drop back to IN_PROGRESS.
       This should not happen in practice (Razorpay payments are final) but
       the status should never be PAID when outstanding > 0.
    4. Partial payment (0 < amount_paid < invoice_amount):
       Keep the current status. OPEN stays OPEN; IN_PROGRESS stays IN_PROGRESS.
       We do NOT introduce a PARTIALLY_PAID state — ``outstanding > 0`` is the
       scorer signal that matters (already on CaseSnapshot).
    5. No payment → keep current status.
    """
    # Full settlement
    if amount_paid + 0.01 >= invoice_amount:
        return InvoiceStatus.PAID

    # Terminal states survive payment changes (a disputed invoice stays disputed
    # even if a partial payment arrives)
    _terminal = {InvoiceStatus.DISPUTED, InvoiceStatus.WRITTEN_OFF, InvoiceStatus.HANDED_OFF}
    if current_status in _terminal:
        return current_status

    # PAID but now shows outstanding — payment must have been revised downward
    # (e.g., credit note applied after the PAID mark). Drop back to IN_PROGRESS
    # so the agent can resume chasing.
    if current_status is InvoiceStatus.PAID:
        return InvoiceStatus.IN_PROGRESS if contacted else InvoiceStatus.OPEN

    # Partial or zero payment: keep whatever the current workflow state is.
    # The agent's contact history determines OPEN vs IN_PROGRESS vs PROMISED.
    return current_status


def promise_is_kept(amount_paid: float, promised_amount: float) -> bool:
    """Return True if the amount paid satisfies the promise.

    Uses a 1 INR tolerance to account for TDS deductions, rounding, and
    common Indian B2B practice of paying a rupee short of the invoiced amount.

    This is the single rule for "kept" -- do not inline this comparison in
    webhook handlers or the batch sweep. Both paths should call this function
    so the rule is changed in one place.
    """
    return amount_paid >= promised_amount - PROMISE_KEPT_TOLERANCE_INR
