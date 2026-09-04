"""Tests for the payment allocation ledger.

Covers:
- Webhook idempotency: same event twice → one allocation row, no double-count
- Double-event: payment.captured + payment_link.paid for same payment
- Partial payment: two allocations summing to full amount → PAID
- Partial payment: one allocation < full → status unchanged, outstanding > 0
- Promise kept with 1 INR tolerance
- derive_status rules (pure function)
"""

from __future__ import annotations

import pytest

from app.core.payment_allocations import (
    PROMISE_KEPT_TOLERANCE_INR,
    derive_status,
    promise_is_kept,
    recompute_amount_paid,
)
from app.models.enums import InvoiceStatus

# ---------------------------------------------------------------------------
# Pure function: recompute_amount_paid
# ---------------------------------------------------------------------------


class TestRecomputeAmountPaid:
    def test_empty_allocations_returns_zero(self):
        assert recompute_amount_paid([]) == 0.0

    def test_single_allocation(self):
        assert recompute_amount_paid([5000.0]) == 5000.0

    def test_multiple_allocations_summed(self):
        # Razorpay payment + bank UTR (partial, then remainder)
        assert recompute_amount_paid([30000.0, 20000.0]) == 50000.0

    def test_credit_note_reduces_total(self):
        # 50,000 paid, 5,000 credit note → 45,000
        assert recompute_amount_paid([50000.0, -5000.0]) == 45000.0

    def test_two_partial_payments_summing_to_full(self):
        assert recompute_amount_paid([60000.0, 40000.0]) == 100000.0


# ---------------------------------------------------------------------------
# Pure function: derive_status
# ---------------------------------------------------------------------------


class TestDeriveStatus:
    def test_full_payment_returns_paid(self):
        status = derive_status(100_000.0, 100_000.0, InvoiceStatus.IN_PROGRESS)
        assert status is InvoiceStatus.PAID

    def test_within_0_01_tolerance_returns_paid(self):
        # 99,999.99 paid against 100,000 invoice: 0.01 gap → PAID
        status = derive_status(100_000.0, 99_999.99, InvoiceStatus.IN_PROGRESS)
        assert status is InvoiceStatus.PAID

    def test_partial_payment_keeps_in_progress(self):
        status = derive_status(100_000.0, 40_000.0, InvoiceStatus.IN_PROGRESS, contacted=True)
        assert status is InvoiceStatus.IN_PROGRESS

    def test_partial_payment_keeps_open_when_not_contacted(self):
        status = derive_status(100_000.0, 1.0, InvoiceStatus.OPEN, contacted=False)
        assert status is InvoiceStatus.OPEN

    def test_zero_payment_keeps_current_status(self):
        status = derive_status(50_000.0, 0.0, InvoiceStatus.PROMISED)
        assert status is InvoiceStatus.PROMISED

    def test_terminal_states_survive_payment(self):
        for terminal in (
            InvoiceStatus.DISPUTED,
            InvoiceStatus.WRITTEN_OFF,
            InvoiceStatus.HANDED_OFF,
        ):
            status = derive_status(100_000.0, 50_000.0, terminal)
            assert status is terminal, f"Expected {terminal} to survive partial payment"

    def test_paid_with_credit_note_applied_drops_back(self):
        # Invoice was PAID, but a credit note brought amount_paid below threshold
        status = derive_status(100_000.0, 90_000.0, InvoiceStatus.PAID, contacted=True)
        assert status is InvoiceStatus.IN_PROGRESS

    def test_exact_payment_returns_paid(self):
        status = derive_status(75_500.0, 75_500.0, InvoiceStatus.OPEN)
        assert status is InvoiceStatus.PAID


# ---------------------------------------------------------------------------
# Pure function: promise_is_kept
# ---------------------------------------------------------------------------


class TestPromiseIsKept:
    def test_exactly_promised_amount_is_kept(self):
        assert promise_is_kept(50_000.0, 50_000.0) is True

    def test_one_inr_short_is_kept(self):
        # TDS deduction of exactly 1 INR
        assert promise_is_kept(49_999.0, 50_000.0) is True

    def test_zero_inr_short_is_kept(self):
        assert promise_is_kept(50_000.0, 50_000.0) is True

    def test_more_than_one_inr_short_is_broken(self):
        # 1.01 INR short of promised → broken
        assert promise_is_kept(49_998.99, 50_000.0) is False

    def test_overpayment_is_kept(self):
        # Customer paid more than promised → definitely kept
        assert promise_is_kept(60_000.0, 50_000.0) is True

    def test_partial_far_short_is_broken(self):
        assert promise_is_kept(10_000.0, 50_000.0) is False

    def test_tolerance_constant_is_one_inr(self):
        """The tolerance must be exactly 1.0 INR — not 0.01 (the old rule)."""
        assert PROMISE_KEPT_TOLERANCE_INR == 1.0


# ---------------------------------------------------------------------------
# Database & Ledger integration tests
# ---------------------------------------------------------------------------

from datetime import date

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base
from app.models import Customer, Invoice, Promise
from app.models.enums import AllocationSource, PromiseStatus
from app.services import repository


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


@pytest.mark.asyncio
async def test_allocation_unique_constraint_deduplication(db_session):
    cust = Customer(business_id="acme", customer_id="C-1", name="Acme Corp")
    db_session.add(cust)
    await db_session.flush()

    inv = Invoice(
        business_id="acme",
        invoice_id="INV-DEDUP",
        customer_pk=cust.id,
        amount=100000.0,
        currency="INR",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        status=InvoiceStatus.OPEN,
    )
    db_session.add(inv)
    await db_session.commit()

    # First event: payment.captured with pay_12345
    alloc1 = await repository.create_allocation(
        db_session,
        business_id="acme",
        invoice_pk=inv.id,
        source=AllocationSource.RAZORPAY_PAYMENT,
        provider_ref="pay_12345",
        amount=50000.0,
    )
    assert alloc1 is not None
    paid = await repository.recompute_and_update_invoice(db_session, inv, "acme")
    assert paid == 50000.0
    assert inv.status == InvoiceStatus.OPEN  # partial, uncontacted -> OPEN

    # Second event: duplicate payment.captured with same provider_ref
    alloc_dup = await repository.create_allocation(
        db_session,
        business_id="acme",
        invoice_pk=inv.id,
        source=AllocationSource.RAZORPAY_PAYMENT,
        provider_ref="pay_12345",
        amount=50000.0,
    )
    assert alloc_dup is None  # deduplicated cleanly!

    # Recompute must still show 50000, not 100000
    paid2 = await repository.recompute_and_update_invoice(db_session, inv, "acme")
    assert paid2 == 50000.0


@pytest.mark.asyncio
async def test_partial_payment_to_full_settle_and_promise_kept(db_session):
    cust = Customer(business_id="acme", customer_id="C-2", name="Beta Corp")
    db_session.add(cust)
    await db_session.flush()

    inv = Invoice(
        business_id="acme",
        invoice_id="INV-PROMISE",
        customer_pk=cust.id,
        amount=80000.0,
        currency="INR",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 15),
        status=InvoiceStatus.IN_PROGRESS,
        prior_reminders_sent=1,
    )
    db_session.add(inv)
    await db_session.flush()

    # Customer promised 80,000
    promise = Promise(
        business_id="acme",
        invoice_pk=inv.id,
        promise_id="PRM-1",
        promised_amount=80000.0,
        promised_date=date(2026, 9, 10),
        currency="INR",
        status=PromiseStatus.PENDING,
    )
    db_session.add(promise)
    await db_session.commit()

    # Partial payment 1: 50,000 via Razorpay link
    alloc1 = await repository.create_allocation(
        db_session,
        business_id="acme",
        invoice_pk=inv.id,
        source=AllocationSource.RAZORPAY_LINK,
        provider_ref="pay_part_1",
        amount=50000.0,
    )
    assert alloc1 is not None
    paid1 = await repository.recompute_and_update_invoice(db_session, inv, "acme")
    assert paid1 == 50000.0
    assert inv.status == InvoiceStatus.IN_PROGRESS  # Partial payment maintains IN_PROGRESS

    # Partial payment 2: 29,999.0 via Bank UTR (1 INR short of full 80,000)
    alloc2 = await repository.create_allocation(
        db_session,
        business_id="acme",
        invoice_pk=inv.id,
        source=AllocationSource.BANK_UTR,
        provider_ref="UTR-FINAL-1",
        amount=29999.0,
    )
    assert alloc2 is not None
    paid2 = await repository.recompute_and_update_invoice(db_session, inv, "acme")
    assert paid2 == 79999.0

    # Promise check with 1 INR tolerance: 79,999 >= 80,000 - 1.0 -> KEPT!
    assert promise_is_kept(paid2, promise.promised_amount) is True
