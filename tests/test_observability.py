"""Wave 3 APM tests: one run-cycle trace, trace_id on logs, request-id echo.

Covered acceptance criteria:

* AC-5 -- ``run_batch_cycle`` produces one root span whose descendants carry
  the per-invoice ``cycle.invoice`` spans with ``score`` / ``gate`` children,
  and the executor's provider calls surface as ``razorpay.*`` / ``resend.*``
  spans (dry-run equivalents, since the gateways are dry-run here).
* AC-6 -- a synthetic OpenTelemetry context produces a structlog record with
  a 32-char hex ``trace_id`` via the logging processor.
* TR-5.2 / TR-5.3 -- ``X-Request-Id`` is echoed (or minted), and health
  endpoints are excluded from auto-instrumentation.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def in_memory_tracing(monkeypatch):
    """Swap the module tracer for an in-memory exporter one.

    ``setup_otel`` installs a console/OTLP tracer at ``app.main`` import time;
    tests that want to *assert* on spans point the module at an in-memory
    tracer instead. Returns (tracer, provider, exporter).
    """

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import app.core.observability as obs

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("wave3-tests")
    monkeypatch.setattr(obs, "_tracer", tracer)
    return tracer, provider, exporter


def _children(span, by_id):
    return [
        s
        for s in by_id.values()
        if s.parent is not None and s.parent.span_id == span.context.span_id
    ]


# ---------------------------------------------------------------------------
# AC-5 (unit part): nested ``with create_span`` forms a real parent/child tree
# ---------------------------------------------------------------------------


def test_nested_create_spans_form_a_parent_child_tree(in_memory_tracing):
    """Regression for flat traces: spans opened inside a span must be children.

    The earlier implementation used ``start_span`` (never current), so every
    invoice/score span landed as a sibling of ``run_batch_cycle`` instead of
    a descendant of it.
    """

    from app.core.observability import create_span

    _, _, exporter = in_memory_tracing

    with create_span("run_batch_cycle") as root, create_span("cycle.invoice", invoice_id="INV-1"):
        with create_span("score"):
            pass
        with create_span("gate", decision_allowed=False):
            pass

    spans = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}
    assert {s.name for s in spans} == {"run_batch_cycle", "cycle.invoice", "score", "gate"}

    root = next(s for s in spans if s.name == "run_batch_cycle")
    assert root.parent is None
    inv = next(s for s in spans if s.name == "cycle.invoice")
    assert inv.parent is not None and inv.parent.span_id == root.context.span_id
    assert inv.attributes.get("invoice_id") == "INV-1"

    children_names = {s.name for s in _children(inv, by_id)}
    assert children_names == {"score", "gate"}


# ---------------------------------------------------------------------------
# AC-6: structlog processor injects trace_id / span_id from the OTel context
# ---------------------------------------------------------------------------


def test_structlog_processor_injects_trace_id_from_synthetic_context(in_memory_tracing):
    from opentelemetry import trace

    from app.core.logging import _add_opentelemetry_processor

    tracer, _, _ = in_memory_tracing
    span = tracer.start_span("log-probe")
    with trace.use_span(span, end_on_exit=True):
        event = _add_opentelemetry_processor(None, None, {"event": "hello"})

    trace_id = event["trace_id"]
    span_id = event["span_id"]
    assert isinstance(trace_id, str) and len(trace_id) == 32
    assert int(trace_id, 16) >= 0  # it is hex
    assert isinstance(span_id, str) and len(span_id) == 16


def test_structlog_processor_is_quiet_outside_a_span(in_memory_tracing):
    from app.core.logging import _add_opentelemetry_processor

    event = _add_opentelemetry_processor(None, None, {"event": "no-span"})
    assert "trace_id" not in event
    assert "span_id" not in event


# ---------------------------------------------------------------------------
# TR-5.2: X-Request-Id pass-through and generation on the real app
# ---------------------------------------------------------------------------


def test_request_id_is_echoed_when_sent_and_minted_when_missing():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    echoed = client.get("/api/v1/health", headers={"X-Request-Id": "req-abc-123"})
    assert echoed.headers.get("X-Request-Id") == "req-abc-123"

    minted = client.get("/api/v1/health")
    rid = minted.headers.get("X-Request-Id") or ""
    assert len(rid) == 32
    assert int(rid, 16) >= 0


# ---------------------------------------------------------------------------
# TR-5.3: health paths excluded from FastAPI auto-instrumentation
# ---------------------------------------------------------------------------


def test_health_paths_are_excluded_from_auto_instrumentation(in_memory_tracing):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _, provider, exporter = in_memory_tracing
    mini = FastAPI()

    @mini.get("/api/v1/health")
    def _health() -> dict:
        return {"status": "healthy"}

    @mini.get("/probe")
    def _probe() -> dict:
        return {"ok": True}

    FastAPIInstrumentor.instrument_app(
        mini, excluded_urls="/api/v1/health", tracer_provider=provider
    )
    client = TestClient(mini)
    for _ in range(3):
        client.get("/api/v1/health")
    for _ in range(2):
        client.get("/probe")

    recorded = [s.name for s in exporter.get_finished_spans()]
    assert recorded.count("GET /probe") == 2
    assert "GET /api/v1/health" not in recorded


# ---------------------------------------------------------------------------
# AC-5 (batch part): a dry-run run_batch_cycle emits the expected span tree.
#
# Two overdue invoices: A was contacted yesterday (policy contact-gap blocks
# the action -> a ``gate`` span with the rejection), B was never contacted
# (approved -> ``gate`` + ``execute_contact`` + provider spans).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_batch_cycle_emits_the_expected_span_tree(in_memory_tracing):
    from datetime import date, timedelta

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.audit import DecisionLedger
    from app.db.session import Base
    from app.models import Customer, Invoice, InvoiceStatus
    from app.services.batch_runner import RunSummary, run_batch_cycle
    from src.ml.versioning import utc_now

    _, _, exporter = in_memory_tracing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    today = date.today()
    async with maker() as session:
        cust_a = Customer(
            business_id="default",
            customer_id="CUST-A",
            name="Blocked Traders Pvt Ltd",
            email="blocked@example.com",
            invoice_count=12,
            avg_days_late=14.0,
        )
        session.add(cust_a)
        await session.flush()
        inv_a = Invoice(
            business_id="default",
            invoice_id="INV-SPAN-A",
            customer_pk=cust_a.id,
            amount=120_000.0,
            currency="INR",
            issue_date=today - timedelta(days=80),
            due_date=today - timedelta(days=40),
            status=InvoiceStatus.OPEN,
            prior_reminders_sent=1,
            last_contact_at=utc_now() - timedelta(days=1),
        )
        cust_b = Customer(
            business_id="default",
            customer_id="CUST-B",
            name="Action Traders Pvt Ltd",
            email="action@example.com",
            invoice_count=30,
            avg_days_late=11.0,
            on_time_ratio_90d=0.4,
        )
        session.add(cust_b)
        await session.flush()
        inv_b = Invoice(
            business_id="default",
            invoice_id="INV-SPAN-B",
            customer_pk=cust_b.id,
            amount=200_000.0,
            currency="INR",
            issue_date=today - timedelta(days=70),
            due_date=today - timedelta(days=35),
            status=InvoiceStatus.OPEN,
        )
        session.add_all([inv_a, inv_b])
        await session.commit()

    async with maker() as session:
        summary = RunSummary(started_at="now")
        await run_batch_cycle(
            session,
            "default",
            limit=10,
            sending_enabled=True,
            ledger=DecisionLedger(),
            summary=summary,
            dry_run=True,
        )

    assert summary.errors == [], f"batch run produced errors: {summary.errors}"
    spans = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}
    names = [s.name for s in spans]

    roots = [s for s in spans if s.parent is None or s.parent.span_id not in by_id]
    assert [s.name for s in roots] == [
        "run_batch_cycle"
    ], f"expected one run_batch_cycle root span, got {[s.name for s in roots]}"

    invoice_spans = [s for s in spans if s.name == "cycle.invoice"]
    assert len(invoice_spans) == 2, names

    for inv in invoice_spans:
        kid_names = {s.name for s in _children(inv, by_id)}
        assert "score" in kid_names, f"cycle.invoice {inv.attributes} has no score child"
        assert "gate" in kid_names, f"cycle.invoice {inv.attributes} has no gate child"

    # Dry-run equivalents of the provider calls surface from the executor.
    assert "execute_contact" in names, names
    assert "razorpay.payment_link.create" in names, names
    assert "resend.emails.send" in names, names

    await engine.dispose()


# ---------------------------------------------------------------------------
# Executor-level: provider calls carry razorpay./resend. span names even with
# dry-run gateways (no real provider in the test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_emits_provider_spans_on_dry_run_execution(in_memory_tracing, monkeypatch):
    from app.core.policy import ActionType, PolicyDecision, ProposedAction
    from app.models.enums import ContactChannel
    from app.services.executor.contract import ExecutionIntent
    from app.services.executor.gateways import (
        DryRunEmailGateway,
        DryRunPaymentGateway,
        Gateways,
    )
    from app.services.executor.service import ExecutionService
    from tests.test_executor import make_case

    _, _, exporter = in_memory_tracing

    case = make_case()
    action = ProposedAction(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.SEND_REMINDER,
        channel=ContactChannel.EMAIL,
        ladder_step="reminder_1",
        requested_discount_pct=0.0,
    )
    decision = PolicyDecision(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.SEND_REMINDER,
        allowed=True,
        effective_discount_pct=0.0,
    )
    intent = ExecutionIntent.from_decision(case, action, decision)

    class _Settings:
        DRY_RUN = True
        REPLY_ADDRESS_SECRET = "test-address-secret"
        REPLY_INBOUND_DOMAIN = "reply.recoup.test"

    service = ExecutionService(
        Gateways(payments=DryRunPaymentGateway(), email=DryRunEmailGateway(), dry_run=True),
        settings=_Settings(),
    )

    class _Invoice:
        id = 1
        business_id = "default"
        payment_link_id: str | None = None
        payment_link_url: str | None = None

    async def _noop_record_contact(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    from app.services import repository

    monkeypatch.setattr(repository, "record_contact", _noop_record_contact)

    result = await service.execute(_Invoice(), intent, _Invoice())  # type: ignore[arg-type]

    names = [s.name for s in exporter.get_finished_spans()]
    assert result.status.value == "simulated"
    assert "razorpay.payment_link.create" in names, names
    assert "resend.emails.send" in names, names
