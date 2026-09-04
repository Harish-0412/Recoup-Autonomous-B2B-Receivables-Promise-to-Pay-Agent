"""Request/response contracts for the payments endpoints.

These schemas cover two surfaces:
 - Bank/UTR payment posting (operator pastes from bank statement)
 - The unmatched review queue
 - Operator confirmation that a UTR belongs to a specific invoice
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BankPaymentIn(BaseModel):
    """An operator-submitted bank payment (NEFT / RTGS / UPI)."""

    model_config = ConfigDict(protected_namespaces=())

    utr: str = Field(
        min_length=1,
        max_length=128,
        description="UTR / transaction reference from the bank statement",
    )
    amount: float = Field(
        gt=0,
        description="Amount received in INR (or the stated currency)",
    )
    currency: str = Field(default="INR", min_length=3, max_length=3)
    paid_on: date = Field(
        description="Bank value date of the payment (YYYY-MM-DD)",
    )
    payer_account: str | None = Field(
        default=None,
        description=(
            "Optional payer account hint: the Recoup customer_id, an IFSC "
            "account number, or a company name. Used for heuristic matching."
        ),
    )
    suggested_invoice_id: str | None = Field(
        default=None,
        description=(
            "Optional operator guess at which invoice this payment settles. "
            "Treated as a strong hint but not forced — the matcher still "
            "validates it is uniquely open before returning 'probable'."
        ),
    )
    notes: str = Field(default="", max_length=500)


class BankPaymentOut(BaseModel):
    """The allocation row created by a bank payment submission, plus matcher verdict."""

    model_config = ConfigDict(protected_namespaces=())

    allocation_id: int
    utr: str
    amount: float
    currency: str
    paid_on: date
    payer_account: str | None
    notes: str
    recorded_by: str
    created_at: datetime

    #: The matcher's confidence in the suggested invoice.
    match_confidence: Literal["duplicate", "probable", "ambiguous", "no_match"]
    match_reason: str
    #: The suggested invoice to confirm, if any. Operator must call
    #: POST /payments/{allocation_id}/allocate to confirm.
    suggested_invoice_id: str | None


class UnmatchedPaymentOut(BaseModel):
    """One unmatched bank UTR in the review queue."""

    model_config = ConfigDict(protected_namespaces=())

    allocation_id: int
    utr: str
    amount: float
    currency: str
    paid_on: date
    payer_account: str | None
    notes: str
    created_at: datetime


class UnmatchedPaymentsResponse(BaseModel):
    """Paginated list of unmatched bank payments."""

    model_config = ConfigDict(protected_namespaces=())

    items: list[UnmatchedPaymentOut]
    total: int


class AllocateIn(BaseModel):
    """Operator confirmation: this UTR belongs to this invoice."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str = Field(
        min_length=1,
        description="The invoice_id (business key) this payment should settle",
    )


class AllocateOut(BaseModel):
    """Result of confirming an allocation."""

    model_config = ConfigDict(protected_namespaces=())

    allocation_id: int
    invoice_id: str
    new_amount_paid: float
    new_outstanding: float
    invoice_status: str
    currency: str
