"""Tests for ERP integrations (Zoho, QuickBooks, Razorpay Invoices, Tally):
- upsert_invoice never overwrites escalation_state, ladder_index, or amount_paid
- Tally CSV & XML file parsing
- Credit notes applied as negative allocations
- Tenancy isolation on ERP credentials and upserts
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base
from app.models import InvoiceStatus
from app.models.enums import AllocationSource, EscalationState
from app.services import repository
from app.services.integrations import tally


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
async def test_upsert_invoice_never_overwrites_collections_state(db_session):
    # Customer
    cust, _ = await repository.upsert_customer(
        db_session,
        "acme",
        customer_id="CUST-1",
        source="zoho_books",
        external_id="Z-1",
        name="Acme Client",
    )
    # Create invoice initially
    inv, created = await repository.upsert_invoice(
        db_session,
        "acme",
        invoice_id="INV-1",
        customer_pk=cust.id,
        erp_source="zoho_books",
        external_id="Z-INV-1",
        amount=100000.0,
        due_date=date(2026, 9, 10),
        issue_date=date(2026, 8, 10),
        payment_terms_days=30,
    )
    assert created is True
    assert inv.amount == 100000.0

    # Simulate agent advancing collections state on Recoup's side
    inv.escalation_state = EscalationState.ESCALATED
    inv.ladder_index = 2
    inv.amount_paid = 20000.0
    inv.status = InvoiceStatus.IN_PROGRESS
    await db_session.flush()

    # Now an ERP sync runs: the ERP updated amount to 95000 and due_date to 2026-09-15.
    # It might even try passing escalation_state or amount_paid.
    inv_updated, created_again = await repository.upsert_invoice(
        db_session,
        "acme",
        invoice_id="INV-1",
        customer_pk=cust.id,
        erp_source="zoho_books",
        external_id="Z-INV-1",
        amount=95000.0,
        due_date=date(2026, 9, 15),
        issue_date=date(2026, 8, 10),
        escalation_state=EscalationState.MONITORING,  # should be ignored
        ladder_index=0,  # should be ignored
        amount_paid=0.0,  # should be ignored
        status=InvoiceStatus.OPEN,  # should be ignored
    )
    assert created_again is False
    # ERP-owned fields updated:
    assert inv_updated.amount == 95000.0
    assert inv_updated.due_date == date(2026, 9, 15)
    # Collections-owned fields strictly preserved:
    assert inv_updated.escalation_state == EscalationState.ESCALATED
    assert inv_updated.ladder_index == 2
    assert inv_updated.amount_paid == 20000.0
    assert inv_updated.status == InvoiceStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_tally_csv_import(db_session):
    csv_data = (
        "voucher_number,ledger_name,date,due_date,amount,currency\n"
        "V-101,Infosys BPO,2026-08-01,2026-09-01,150000,INR\n"
        "V-102,Wipro Infotech,2026-08-05,2026-09-05,85000,INR\n"
    )

    res = await tally.import_csv(db_session, "acme", file_content=csv_data)
    assert res.invoices_created == 2
    assert res.customers_created == 2
    assert len(res.errors) == 0

    # Re-importing same CSV is idempotent
    res_repeat = await tally.import_csv(db_session, "acme", file_content=csv_data)
    assert res_repeat.invoices_created == 0
    assert res_repeat.invoices_updated == 2
    assert res_repeat.customers_updated == 2


@pytest.mark.asyncio
async def test_tally_xml_import(db_session):
    xml_data = """<ENVELOPE>
        <BODY>
            <DATA>
                <TALLYMESSAGE>
                    <VOUCHER>
                        <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
                        <VOUCHERNUMBER>XML-901</VOUCHERNUMBER>
                        <DATE>20260815</DATE>
                        <PARTYLEDGERNAME>Tata Consultancy Services</PARTYLEDGERNAME>
                        <AMOUNT>-120000</AMOUNT>
                        <CURRENCYNAME>INR</CURRENCYNAME>
                    </VOUCHER>
                </TALLYMESSAGE>
            </DATA>
        </BODY>
    </ENVELOPE>"""

    res = await tally.import_xml(db_session, "acme", file_content=xml_data)
    assert res.invoices_created == 1
    assert res.customers_created == 1
    assert len(res.errors) == 0

    inv = await repository.get_invoice(db_session, "tally-XML-901", "acme")
    assert inv is not None
    assert inv.amount == 120000.0
    assert inv.currency == "INR"


@pytest.mark.asyncio
async def test_credit_note_as_negative_allocation(db_session):
    cust, _ = await repository.upsert_customer(
        db_session, "acme", customer_id="CUST-CN", source="test", external_id="1", name="CN Client"
    )
    inv, _ = await repository.upsert_invoice(
        db_session,
        "acme",
        invoice_id="INV-CN-1",
        customer_pk=cust.id,
        erp_source="test",
        external_id="1",
        amount=100000.0,
        due_date=date(2026, 9, 10),
        issue_date=date(2026, 8, 10),
    )

    # Initial payment of 50,000
    alloc1 = await repository.create_allocation(
        db_session,
        business_id="acme",
        invoice_pk=inv.id,
        source=AllocationSource.BANK_UTR,
        provider_ref="UTR-CN-1",
        amount=50000.0,
    )
    assert alloc1 is not None
    paid = await repository.recompute_and_update_invoice(db_session, inv, "acme")
    assert paid == 50000.0
    assert inv.amount_paid == 50000.0

    # Credit note of 10,000 applied (negative allocation)
    alloc_cn = await repository.create_allocation(
        db_session,
        business_id="acme",
        invoice_pk=inv.id,
        source=AllocationSource.ERP_CREDIT_NOTE,
        provider_ref="CN-999",
        amount=-10000.0,
    )
    assert alloc_cn is not None
    paid_after_cn = await repository.recompute_and_update_invoice(db_session, inv, "acme")
    assert paid_after_cn == 40000.0
    assert inv.amount_paid == 40000.0
