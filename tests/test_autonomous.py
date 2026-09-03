"""The autonomous run: authentication, locking, the sweep, and the kill switch.

Each of these guards a way an unattended agent can do damage:

* an unauthenticated trigger is an unauthenticated way to mail a whole book;
* two overlapping runs both read the same "contacts so far" and both decide the
  cap allows one more, so the customer gets two emails;
* a lapsed promise nobody marks broken silences the agent on that invoice
  forever, because the policy gate stays quiet while a promise is open;
* a kill switch that advances the ladder while halting the send is not a kill
  switch -- flipping it back on would find every invoice a rung further along
  with nothing having been delivered.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.core.promise_tracker import PromiseRecord
from app.core.security import require_task_key
from app.models.enums import InvoiceStatus, PromiseStatus
from app.services.batch_runner import BATCH_LOCK, RunSummary, sweep_promises
from app.services.locks import lock_key
from src.ml.versioning import utc_now

# --- authentication ---------------------------------------------------------


@pytest.fixture
def configured_key(monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "the-cron-secret")
    yield "the-cron-secret"
    get_settings.cache_clear()


def test_the_correct_bearer_token_is_accepted(configured_key):
    require_task_key(f"Bearer {configured_key}")  # must not raise


@pytest.mark.parametrize(
    "header",
    ["", "Bearer wrong", "wrong", "Basic the-cron-secret", "the-cron-secret"],
)
def test_anything_else_is_refused(configured_key, header):
    with pytest.raises(HTTPException) as caught:
        require_task_key(header)

    assert caught.value.status_code == 401


def test_an_unconfigured_key_denies_rather_than_allows(monkeypatch):
    """A deploy that forgot to set the key must fail visibly, not run open."""

    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TASK_API_KEY", "")
    try:
        with pytest.raises(HTTPException) as caught:
            require_task_key("Bearer anything")
        assert caught.value.status_code == 503
    finally:
        get_settings.cache_clear()


# --- the lock ---------------------------------------------------------------


def test_a_lock_name_maps_to_a_stable_positive_key():
    """pg_try_advisory_lock takes a signed bigint."""

    key = lock_key(BATCH_LOCK)

    assert key == lock_key(BATCH_LOCK)
    assert 0 < key <= 0x7FFFFFFFFFFFFFFF


def test_different_names_do_not_collide():
    assert lock_key("recoup:batch-run") != lock_key("recoup:promise-sweep")


# --- the promise sweep ------------------------------------------------------


class FakePromiseRow:
    def __init__(self, promise_id, amount, promised_date):
        self.promise_id = promise_id
        self.promised_amount = amount
        self.promised_date = promised_date
        self.currency = "INR"
        self.source_reply_id = "r1"
        self.source_confidence = 0.9
        self.status = PromiseStatus.PENDING
        # NOT NULL in the real schema; a None here would only test my double.
        self.created_at = utc_now()
        self.resolved_at = None


class FakeInvoiceRow:
    def __init__(self, invoice_id, amount, amount_paid=0.0, status=InvoiceStatus.PROMISED):
        self.invoice_id = invoice_id
        self.amount = amount
        self.amount_paid = amount_paid
        self.status = status


@pytest.fixture
def sweep(monkeypatch):
    """Drive sweep_promises over a fixed set of rows, without a database."""

    async def run(pairs, as_of):
        from app.services import repository

        async def fake_pending(session, limit=500):
            return pairs

        monkeypatch.setattr(repository, "pending_promises", fake_pending)
        return await sweep_promises(None, as_of=as_of)

    return run


@pytest.mark.anyio
async def test_a_lapsed_unpaid_promise_is_broken_and_releases_the_invoice(sweep):
    """The whole point of the sweep: this is what re-arms escalation."""

    today = date(2026, 9, 1)
    promise = FakePromiseRow("PRM-1", 100_000.0, today - timedelta(days=10))
    invoice = FakeInvoiceRow("INV-1", 100_000.0, amount_paid=0.0)

    checked, broken, kept = await sweep([(promise, invoice)], today)

    assert (checked, broken, kept) == (1, 1, 0)
    assert promise.status is PromiseStatus.BROKEN
    assert promise.resolved_at is not None
    # Released from PROMISED, so the policy gate stops being quiet about it.
    assert invoice.status is InvoiceStatus.IN_PROGRESS


@pytest.mark.anyio
async def test_a_promise_settled_by_payment_is_kept(sweep):
    """Only money that actually arrived settles a promise."""

    today = date(2026, 9, 1)
    promise = FakePromiseRow("PRM-2", 100_000.0, today - timedelta(days=10))
    invoice = FakeInvoiceRow("INV-2", 100_000.0, amount_paid=100_000.0)

    checked, broken, kept = await sweep([(promise, invoice)], today)

    assert (checked, broken, kept) == (1, 0, 1)
    assert promise.status is PromiseStatus.KEPT
    # Still PROMISED: moving it to PAID is the payment webhook's job, and this
    # function must not infer payment state of its own.
    assert invoice.status is InvoiceStatus.PROMISED


@pytest.mark.anyio
async def test_a_promise_still_in_the_future_is_left_alone(sweep):
    today = date(2026, 9, 1)
    promise = FakePromiseRow("PRM-3", 100_000.0, today + timedelta(days=10))
    invoice = FakeInvoiceRow("INV-3", 100_000.0)

    checked, broken, kept = await sweep([(promise, invoice)], today)

    assert (checked, broken, kept) == (1, 0, 0)
    assert promise.status is PromiseStatus.PENDING
    assert invoice.status is InvoiceStatus.PROMISED


@pytest.mark.anyio
async def test_the_sweep_handles_a_mixed_book(sweep):
    today = date(2026, 9, 1)
    pairs = [
        (
            FakePromiseRow("PRM-lapsed", 50_000.0, today - timedelta(days=5)),
            FakeInvoiceRow("INV-a", 50_000.0),
        ),
        (
            FakePromiseRow("PRM-paid", 50_000.0, today - timedelta(days=5)),
            FakeInvoiceRow("INV-b", 50_000.0, amount_paid=50_000.0),
        ),
        (
            FakePromiseRow("PRM-future", 50_000.0, today + timedelta(days=5)),
            FakeInvoiceRow("INV-c", 50_000.0),
        ),
    ]

    checked, broken, kept = await sweep(pairs, today)

    assert (checked, broken, kept) == (3, 1, 1)


@pytest.mark.anyio
async def test_a_partial_payment_does_not_keep_a_promise(sweep):
    """Half the money is not the promise being kept."""

    today = date(2026, 9, 1)
    promise = FakePromiseRow("PRM-4", 100_000.0, today - timedelta(days=10))
    invoice = FakeInvoiceRow("INV-4", 100_000.0, amount_paid=40_000.0)

    _, broken, kept = await sweep([(promise, invoice)], today)

    assert (broken, kept) == (1, 0)


# --- the run summary --------------------------------------------------------


def test_a_skipped_run_is_reported_as_skipped_not_as_empty():
    """A cron log must distinguish "nothing to do" from "someone else is doing it"."""

    summary = RunSummary(started_at="now", ran=False, skipped_reason="lock held")

    assert summary.ran is False
    assert summary.scored == 0
    assert summary.skipped_reason


def test_a_grace_period_applies_before_a_promise_is_broken():
    """Payments settle and webhooks lag; breaking at one minute past midnight
    would punish customers who actually paid."""

    from app.core.promise_tracker import DEFAULT_GRACE_DAYS, assess_promise

    today = date(2026, 9, 1)
    promise = PromiseRecord(
        invoice_id="INV-5",
        promised_amount=10_000.0,
        promised_date=today - timedelta(days=max(DEFAULT_GRACE_DAYS - 1, 0)),
        currency="INR",
    )

    outcome = assess_promise(promise, amount_paid=0.0, as_of=today)

    assert outcome.status is PromiseStatus.PENDING
