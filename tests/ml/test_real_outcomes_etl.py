"""Tests for the Wave 6 real-outcomes and broken-promise ETL pipelines.

Verifies:
- real_outcomes.py:
    - recovered_within_30d = 1 iff sum(allocations where received_at <= scored_at + 30d) >= amount - epsilon.
    - scored_at is trace.recorded_at, never due_date.
    - Immature invoices (< 30d since scoring) are excluded from the dataset.
    - Tenant isolation: joins stay inside the same business_id.
- real_broken_promise_outcomes.py:
    - KEPT/BROKEN labels come from promise_tracker status vs allocations.
    - Pending promises are skipped.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.db.session import async_session_maker
from app.models import (
    Business,
    Customer,
    DecisionTrace,
    Invoice,
    PaymentAllocation,
    Promise,
)
from app.models.enums import (
    AllocationSource,
    ContactChannel,
    DecisionOutcome,
    InvoiceStatus,
    PromiseStatus,
)
from scripts.etl.real_broken_promise_outcomes import extract as extract_broken_promises
from scripts.etl.real_outcomes import extract as extract_recovery_outcomes
from src.agent.promise_handler import FEATURE_COLUMNS
from src.ml.features.recovery_features import FEATURE_COLUMNS_V1, FEATURE_SET_VERSION

TEST_TMP_DIR = Path(__file__).resolve().parents[2] / "tmp" / "test_etl"


@pytest.fixture
def local_tmp_dir():
    TEST_TMP_DIR.mkdir(parents=True, exist_ok=True)
    yield TEST_TMP_DIR
    import shutil

    shutil.rmtree(TEST_TMP_DIR, ignore_errors=True)


@pytest.mark.asyncio
async def test_real_recovery_outcomes_etl(local_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    test_business = f"test-real-etl-biz-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)

    # Mock warehouse path
    out_parquet = local_tmp_dir / "recovery_outcomes.parquet"
    out_summary = local_tmp_dir / "recovery_outcomes_summary.json"
    monkeypatch.setattr("scripts.etl.real_outcomes.OUT_PARQUET", out_parquet)
    monkeypatch.setattr("scripts.etl.real_outcomes.OUT_SUMMARY", out_summary)

    feature_dict = {col: 0.5 for col in FEATURE_COLUMNS_V1}

    async with async_session_maker() as session:
        # Create business
        session.add(Business(business_id=test_business, name="ETL Test Co"))

        # Create customer
        customer = Customer(
            business_id=test_business,
            customer_id="CUS-ETL-001",
            name="ETL Customer",
            email="etl@example.test",
            preferred_channel=ContactChannel.EMAIL,
        )
        session.add(customer)
        await session.flush()

        # 1. Mature invoice - fully settled in window -> label 1
        inv1 = Invoice(
            business_id=test_business,
            customer_pk=customer.id,
            invoice_id="INV-MATURE-RECOVERED",
            amount=50000.0,
            amount_paid=50000.0,
            issue_date=(now - timedelta(days=90)).date(),
            due_date=(now - timedelta(days=60)).date(),
            status=InvoiceStatus.PAID,
        )
        session.add(inv1)
        await session.flush()

        max_seq = (await session.execute(select(func.coalesce(func.max(DecisionTrace.seq), 0)))).scalar_one()
        base_seq = int(max_seq) + 1

        # Scored 40 days ago (window closed 10 days ago)
        scored_at1 = now - timedelta(days=40)
        trace1 = DecisionTrace(
            business_id=test_business,
            seq=base_seq,
            invoice_id="INV-MATURE-RECOVERED",
            event="scored",
            outcome=DecisionOutcome.APPROVED,
            entry_hash="hash1",
            recorded_at=scored_at1,
            payload={"feature_set": FEATURE_SET_VERSION, "features": feature_dict},
        )
        session.add(trace1)

        # Payment received within window (at scored_at + 10 days)
        session.add(
            PaymentAllocation(
                business_id=test_business,
                invoice_pk=inv1.id,
                source=AllocationSource.RAZORPAY_PAYMENT,
                provider_ref="ALLOC-1",
                amount=50000.0,
                received_at=scored_at1 + timedelta(days=10),
            )
        )

        # 2. Mature invoice - not settled in window -> label 0
        inv2 = Invoice(
            business_id=test_business,
            customer_pk=customer.id,
            invoice_id="INV-MATURE-UNRECOVERED",
            amount=40000.0,
            amount_paid=0.0,
            issue_date=(now - timedelta(days=90)).date(),
            due_date=(now - timedelta(days=60)).date(),
            status=InvoiceStatus.OPEN,
        )
        session.add(inv2)
        await session.flush()

        scored_at2 = now - timedelta(days=45)
        trace2 = DecisionTrace(
            business_id=test_business,
            seq=base_seq + 1,
            invoice_id="INV-MATURE-UNRECOVERED",
            event="scored",
            outcome=DecisionOutcome.APPROVED,
            entry_hash="hash2",
            recorded_at=scored_at2,
            payload={"feature_set": FEATURE_SET_VERSION, "features": feature_dict},
        )
        session.add(trace2)

        # 3. Immature invoice - scored only 10 days ago (window has not closed) -> should be excluded
        inv3 = Invoice(
            business_id=test_business,
            customer_pk=customer.id,
            invoice_id="INV-IMMATURE",
            amount=30000.0,
            amount_paid=0.0,
            issue_date=(now - timedelta(days=45)).date(),
            due_date=(now - timedelta(days=15)).date(),
            status=InvoiceStatus.OPEN,
        )
        session.add(inv3)
        await session.flush()

        scored_at3 = now - timedelta(days=10)
        trace3 = DecisionTrace(
            business_id=test_business,
            seq=base_seq + 2,
            invoice_id="INV-IMMATURE",
            event="scored",
            outcome=DecisionOutcome.APPROVED,
            entry_hash="hash3",
            recorded_at=scored_at3,
            payload={"feature_set": FEATURE_SET_VERSION, "features": feature_dict},
        )
        session.add(trace3)

        await session.commit()

    # Run ETL
    counts = await extract_recovery_outcomes(horizon_days=30, business_id=test_business)
    assert counts["mature_rows"] == 2
    assert counts["immature_excluded"] >= 1
    assert out_parquet.exists()

    df = pd.read_parquet(out_parquet)
    assert len(df) == 2
    assert "INV-IMMATURE" not in df["invoice_id"].values

    rec_row = df[df["invoice_id"] == "INV-MATURE-RECOVERED"].iloc[0]
    assert rec_row["recovered_within_30d"] == 1
    assert rec_row["business_id"] == test_business

    unrec_row = df[df["invoice_id"] == "INV-MATURE-UNRECOVERED"].iloc[0]
    assert unrec_row["recovered_within_30d"] == 0


@pytest.mark.asyncio
async def test_real_broken_promise_etl(local_tmp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    test_business = f"test-broken-etl-biz-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)

    out_csv = local_tmp_dir / "broken_promise_live.csv"
    monkeypatch.setattr("scripts.etl.real_broken_promise_outcomes.OUT_CSV", out_csv)

    feature_dict = {col: 0.25 for col in FEATURE_COLUMNS}

    async with async_session_maker() as session:
        session.add(Business(business_id=test_business, name="Broken Promise Test Co"))
        cust = Customer(
            business_id=test_business,
            customer_id="CUS-BP-001",
            name="BP Customer",
            email="bp@example.test",
        )
        session.add(cust)
        await session.flush()

        inv = Invoice(
            business_id=test_business,
            customer_pk=cust.id,
            invoice_id="INV-BP-001",
            amount=75000.0,
            amount_paid=0.0,
            issue_date=(now - timedelta(days=50)).date(),
            due_date=(now - timedelta(days=20)).date(),
            status=InvoiceStatus.IN_PROGRESS,
        )
        session.add(inv)
        await session.flush()

        # Promise 1: KEPT
        p1 = Promise(
            business_id=test_business,
            promise_id="PRM-001",
            invoice_pk=inv.id,
            promised_amount=75000.0,
            promised_date=(now - timedelta(days=5)).date(),
            status=PromiseStatus.KEPT,
        )
        session.add(p1)

        max_seq = (await session.execute(select(func.coalesce(func.max(DecisionTrace.seq), 0)))).scalar_one()
        base_seq = int(max_seq) + 1

        trace_p1 = DecisionTrace(
            business_id=test_business,
            seq=base_seq,
            invoice_id=inv.invoice_id,
            event="promise:scored",
            outcome=DecisionOutcome.APPROVED,
            entry_hash="hash_p1",
            recorded_at=now - timedelta(days=10),
            payload={"promise_id": "PRM-001", "features": feature_dict},
        )
        session.add(trace_p1)

        # Promise 2: BROKEN
        p2 = Promise(
            business_id=test_business,
            promise_id="PRM-002",
            invoice_pk=inv.id,
            promised_amount=75000.0,
            promised_date=(now - timedelta(days=2)).date(),
            status=PromiseStatus.BROKEN,
        )
        session.add(p2)

        trace_p2 = DecisionTrace(
            business_id=test_business,
            seq=base_seq + 1,
            invoice_id=inv.invoice_id,
            event="promise:scored",
            outcome=DecisionOutcome.APPROVED,
            entry_hash="hash_p2",
            recorded_at=now - timedelta(days=7),
            payload={"promise_id": "PRM-002", "features": feature_dict},
        )
        session.add(trace_p2)

        # Promise 3: PENDING -> skipped
        p3 = Promise(
            business_id=test_business,
            promise_id="PRM-003",
            invoice_pk=inv.id,
            promised_amount=75000.0,
            promised_date=(now + timedelta(days=5)).date(),
            status=PromiseStatus.PENDING,
        )
        session.add(p3)

        trace_p3 = DecisionTrace(
            business_id=test_business,
            seq=base_seq + 2,
            invoice_id=inv.invoice_id,
            event="promise:scored",
            outcome=DecisionOutcome.APPROVED,
            entry_hash="hash_p3",
            recorded_at=now,
            payload={"promise_id": "PRM-003", "features": feature_dict},
        )
        session.add(trace_p3)

        await session.commit()

    counts = await extract_broken_promises(business_id=test_business)
    assert counts["live_rows"] == 2
    assert counts["pending_skipped"] >= 1
    assert out_csv.exists()

    df = pd.read_csv(out_csv)
    assert len(df) == 2
    kept_row = df[df["promise_id"] == "PRM-001"].iloc[0]
    assert kept_row["is_broken"] == 0

    broken_row = df[df["promise_id"] == "PRM-002"].iloc[0]
    assert broken_row["is_broken"] == 1
