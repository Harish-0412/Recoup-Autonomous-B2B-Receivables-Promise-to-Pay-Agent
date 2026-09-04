"""Serve-side drift features: database rows into the shared schema.

This is the serving half of the train/serve parity contract. Training
builds ``PeriodSeries`` from UCI monthly histories; this module builds the
* same* ``PeriodSeries`` from a customer's trailing window, split into
three 30-day periods, oldest first. The nine features themselves are
computed by ``src.ml.drift.features.from_period_series`` -- nothing here
reimplements them.

Two documented approximations, both forced by what the schema stores:

* **Paid attribution.** Invoices carry ``amount_paid`` plus a single
  ``paid_at`` timestamp, not a payment schedule, so a whole invoice's paid
  amount is attributed to the period it settled in. Partial payments
  landing across periods are pulled into the settlement period.
* **Delay signal.** Settled invoices contribute their measured delay;
  additionally, open overdue invoices contribute their *current* lateness
  to the most recent period, so active degradation shows up before
  anything settles. Without this a customer paying nothing would look
  perfectly stable.
* **Limit proxy.** Customers have no credit limit, so ``limit`` is the
  lifetime billing scale (average invoice × invoice count): utilization
  reads as "current 90-day billing against lifetime scale".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from src.ml.drift.features import DAYS_PER_MONTH, PeriodSeries


@dataclass(frozen=True)
class InvoiceFacts:
    """The invoice fields drift scoring reads. Plain values, no ORM."""

    amount: float
    amount_paid: float
    issue_date: date
    due_date: date
    paid_at: datetime | None


def _window_bounds(as_of: date, window_days: int, periods: int) -> list[tuple[date, date]]:
    span = window_days // periods
    bounds = []
    for i in range(periods):
        end = as_of - timedelta(days=span * (periods - 1 - i))
        start = end - timedelta(days=span) + timedelta(days=1)
        bounds.append((start, end))
    return bounds


def customer_series(
    invoices: Sequence[InvoiceFacts],
    *,
    lifetime_scale: float,
    as_of: date,
    window_days: int = 90,
    periods: int = 3,
) -> PeriodSeries:
    """Assemble one customer's trailing-window period series."""

    bounds = _window_bounds(as_of, window_days, periods)
    billed = [0.0] * periods
    paid = [0.0] * periods
    delays: list[list[float]] = [[] for _ in range(periods)]

    for invoice in invoices:
        for index, (start, end) in enumerate(bounds):
            if start <= invoice.issue_date <= end:
                billed[index] += max(invoice.amount, 0.0)
        if invoice.paid_at is not None:
            settled = invoice.paid_at.date()
            for index, (start, end) in enumerate(bounds):
                if start <= settled <= end:
                    paid[index] += max(invoice.amount_paid, 0.0)
                    late_days = max((settled - invoice.due_date).days, 0)
                    delays[index].append(late_days / DAYS_PER_MONTH)

    # Active degradation: open overdue invoices age into the latest period.
    for invoice in invoices:
        if invoice.paid_at is None and invoice.due_date < as_of:
            late_days = max((as_of - invoice.due_date).days, 0)
            delays[-1].append(late_days / DAYS_PER_MONTH)

    mean_delays = tuple((sum(bucket) / len(bucket)) if bucket else 0.0 for bucket in delays)
    return PeriodSeries(
        delays_months=mean_delays,
        paid=tuple(paid),
        billed=tuple(billed),
        limit=max(float(lifetime_scale), 1.0),
    )
