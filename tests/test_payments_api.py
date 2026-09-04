"""Tests for the payments API endpoints:
- POST /api/v1/payments/bank (UTR payment intake)
- GET /api/v1/payments/unmatched (review queue)
- POST /api/v1/payments/{allocation_id}/allocate (human confirmation)
- Tenancy isolation on payment allocations
"""

from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import require_api_key
from app.core.tenancy import TenantContext, require_tenant
from app.db.session import Base, get_db
from app.main import app
from app.models import Customer, Invoice, InvoiceStatus


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


@pytest_asyncio.fixture
async def client_factory(db_session):
    def _make_client(business_id: str = "default"):
        async def _override_db():
            yield db_session

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_api_key] = lambda: None
        app.dependency_overrides[require_tenant] = lambda: TenantContext(business_id=business_id)

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    yield _make_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_post_bank_payment_creates_unmatched_allocation(db_session, client_factory):
    # Seed customer and invoice
    cust = Customer(
        business_id="acme",
        customer_id="CUST-1",
        name="Acme Corp",
    )
    db_session.add(cust)
    await db_session.flush()

    inv = Invoice(
        business_id="acme",
        invoice_id="INV-100",
        customer_pk=cust.id,
        amount=50000.0,
        currency="INR",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 4),
        status=InvoiceStatus.OPEN,
    )
    db_session.add(inv)
    await db_session.commit()

    async with client_factory("acme") as client:
        res = await client.post(
            "/api/v1/payments/bank",
            json={
                "utr": "UTR123456789",
                "amount": 50000.0,
                "currency": "INR",
                "paid_on": "2026-09-04",
                "suggested_invoice_id": "INV-100",
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["utr"] == "UTR123456789"
        assert data["amount"] == 50000.0
        assert data["match_confidence"] == "probable"
        assert data["suggested_invoice_id"] == "INV-100"
        alloc_id = data["allocation_id"]

        # Duplicate UTR should fail with 409
        res_dup = await client.post(
            "/api/v1/payments/bank",
            json={
                "utr": "UTR123456789",
                "amount": 50000.0,
                "currency": "INR",
                "paid_on": "2026-09-04",
            },
        )
        assert res_dup.status_code == 409

        # Check in review queue
        res_unmatched = await client.get("/api/v1/payments/unmatched")
        assert res_unmatched.status_code == 200
        unmatched = res_unmatched.json()
        assert unmatched["total"] == 1
        assert unmatched["items"][0]["allocation_id"] == alloc_id

        # Now confirm allocation to INV-100
        res_alloc = await client.post(
            f"/api/v1/payments/{alloc_id}/allocate",
            json={"invoice_id": "INV-100"},
        )
        assert res_alloc.status_code == 200
        alloc_data = res_alloc.json()
        assert alloc_data["new_amount_paid"] == 50000.0
        assert alloc_data["new_outstanding"] == 0.0
        assert alloc_data["invoice_status"] == "PAID"

        # Review queue should now be empty
        res_empty = await client.get("/api/v1/payments/unmatched")
        assert res_empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_cross_tenant_isolation_on_allocation(db_session, client_factory):
    # Customer and invoice in tenant A
    cust_a = Customer(business_id="tenant_a", customer_id="CUST-A", name="A Corp")
    db_session.add(cust_a)
    await db_session.flush()

    inv_a = Invoice(
        business_id="tenant_a",
        invoice_id="INV-A",
        customer_pk=cust_a.id,
        amount=25000.0,
        currency="INR",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, 9, 4),
        status=InvoiceStatus.OPEN,
    )
    db_session.add(inv_a)
    await db_session.commit()

    # Post payment in tenant B
    async with client_factory("tenant_b") as client_b:
        res = await client_b.post(
            "/api/v1/payments/bank",
            json={
                "utr": "UTR-B-1",
                "amount": 25000.0,
                "currency": "INR",
                "paid_on": "2026-09-04",
            },
        )
        assert res.status_code == 201
        alloc_b_id = res.json()["allocation_id"]

        # Attempt to allocate tenant B's payment to tenant A's invoice INV-A
        res_leak = await client_b.post(
            f"/api/v1/payments/{alloc_b_id}/allocate",
            json={"invoice_id": "INV-A"},
        )
        assert res_leak.status_code == 404
