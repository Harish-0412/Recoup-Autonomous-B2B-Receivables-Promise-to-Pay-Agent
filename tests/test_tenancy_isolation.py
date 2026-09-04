"""Wave 1 row-level isolation: one database, two businesses, no cross-reads.

Done criteria covered here:
- run-cycle on A never reads B's invoices (list_open_invoices / load_case scope)
- forged reply-to for A's invoice on B's tag is rejected
- webhook for A's link does not mutate B (payment-link lookup scoped)
- repository calls without business_id fail loudly (wrapper, not convention)
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.session import Base
from app.models import Customer, Invoice, InvoiceStatus
from app.services import repository
from app.services.reply_routing import reply_address, resolve_invoice_id, resolve_tenant_invoice

A = "acme"
B = "globex"
SECRET = "test-tenant-secret"
DOMAIN = "reply.recoup.test"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        yield sess
    await engine.dispose()


async def _seed_business(sess: AsyncSession, business_id: str) -> Invoice:
    customer = Customer(
        business_id=business_id,
        customer_id="C-1",
        name=f"{business_id} Traders",
        email=f"ap@{business_id}.example",
    )
    sess.add(customer)
    await sess.flush()
    today = date(2026, 9, 1)
    invoice = Invoice(
        business_id=business_id,
        invoice_id="INV-1042",
        customer_pk=customer.id,
        amount=100_000.0,
        currency="INR",
        issue_date=today - timedelta(days=50),
        due_date=today - timedelta(days=20),
        status=InvoiceStatus.OPEN,
    )
    sess.add(invoice)
    await sess.flush()
    return invoice


async def test_same_keys_coexist_in_two_businesses(session: AsyncSession):
    await _seed_business(session, A)
    await _seed_business(session, B)
    await session.commit()

    inv_a = await repository.get_invoice(session, "INV-1042", A)
    inv_b = await repository.get_invoice(session, "INV-1042", B)
    assert inv_a is not None and inv_a.business_id == A
    assert inv_b is not None and inv_b.business_id == B
    assert inv_a.id != inv_b.id


async def test_run_cycle_scope_never_reads_other_tenant(session: AsyncSession):
    await _seed_business(session, A)
    await _seed_business(session, B)
    await session.commit()

    open_a = await repository.list_open_invoices(session, A)
    open_b = await repository.list_open_invoices(session, B)
    assert [i.invoice_id for i in open_a] == ["INV-1042"]
    assert all(i.business_id == A for i in open_a)
    assert all(i.business_id == B for i in open_b)

    case_a = await repository.load_case(session, open_a[0], A)
    assert case_a.invoice.invoice_id == "INV-1042"
    # Loading A's row under B's scope is a cross-tenant write-guard violation.
    with pytest.raises(ValueError, match="Cross-tenant"):
        await repository.load_case(session, open_a[0], B)


async def test_forged_reply_to_across_businesses_is_rejected():
    addr_a = reply_address("INV-1042", secret=SECRET, domain=DOMAIN, business_id=A)
    assert resolve_tenant_invoice(addr_a, secret=SECRET) == (A, "INV-1042")
    # Presenting A's tag as B fails the HMAC (tag binds business + invoice).
    tag = addr_a.split(".")[-1].split("@")[0]
    forged = f"reply+{B}.INV-1042.{tag}@{DOMAIN}"
    assert resolve_tenant_invoice(forged, secret=SECRET) is None
    assert resolve_invoice_id(addr_a, secret=SECRET, expected_business_id=B) is None
    assert resolve_invoice_id(addr_a, secret=SECRET, expected_business_id=A) == "INV-1042"


async def test_payment_link_lookup_does_not_cross_tenants(session: AsyncSession):
    inv_a = await _seed_business(session, A)
    inv_b = await _seed_business(session, B)
    # Colliding link ids across tenants must still resolve per-tenant.
    inv_a.payment_link_id = "plink_SAME"
    inv_b.payment_link_id = "plink_SAME"
    await session.commit()

    found_a = await repository.get_invoice_by_payment_link(session, "plink_SAME", A)
    found_b = await repository.get_invoice_by_payment_link(session, "plink_SAME", B)
    assert found_a is not None and found_a.business_id == A
    assert found_b is not None and found_b.business_id == B
    # A's webhook scope can never return B's row: mutating found_a leaves B.
    found_a.amount_paid += 100_000.0
    assert inv_b.amount_paid == 0.0


async def test_repository_requires_business_id(session: AsyncSession):
    await _seed_business(session, A)
    await session.commit()
    with pytest.raises((ValueError, TypeError)):
        await repository.get_invoice(session, "INV-1042")  # type: ignore[call-arg]
    with pytest.raises((ValueError, TypeError)):
        await repository.list_open_invoices(session)  # type: ignore[call-arg]


async def test_webhook_idempotency_is_per_tenant(session: AsyncSession):
    from app.models import WebhookEvent

    session.add(WebhookEvent(business_id=A, event_id="evt_1", event_type="payment.captured"))
    await session.commit()
    assert await repository.webhook_already_processed(session, "evt_1", A) is True
    assert await repository.webhook_already_processed(session, "evt_1", B) is False


def test_tenant_settings_default_business_id():
    assert get_settings().BUSINESS_ID == "default"
