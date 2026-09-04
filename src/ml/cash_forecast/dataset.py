"""Training rows for the cash forecast, split by time rather than at random.

Two disciplines, same as the recovery dataset:

**Temporal splits.** Train on invoices flagged early, validate in the middle,
test on the most recent. A random split would let lag tables learn payment
timing from invoices flagged *after* the ones they are tested on.

**Only mature, observed lags.** A lag row needs an actual paid date. Invoices
still inside their horizon have no observed outcome; invoices whose payment
landed past the horizon are right-censored (``recovered=False``,
``recovered_date=None``) and excluded -- their mass belongs to the recovery
probability, not to the lag table. Both exclusions use the same
``mature_invoices`` cut the recovery dataset uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.data.synthetic_generator import Customer, Invoice, SyntheticBatch
from src.ml.contact_timing.segments import segment_id


@dataclass(frozen=True)
class LagObservation:
    """One realised payment lag, with the observables that segment it."""

    invoice_id: str
    segment: str
    lag_days: int
    amount: float
    flagged_date: date
    recovered: bool


@dataclass(frozen=True)
class LagSplit:
    """One temporal slice of lag observations."""

    name: str
    rows: tuple[LagObservation, ...]

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def recovered_lags(self) -> list[tuple[str, int]]:
        """(segment, lag) pairs for invoices actually observed paid."""

        return [(row.segment, row.lag_days) for row in self.rows if row.recovered]


@dataclass(frozen=True)
class LagDataset:
    """Train / validation / test slices plus the provenance to reproduce them."""

    train: LagSplit
    validation: LagSplit
    test: LagSplit
    horizon_days: int
    seed: int
    train_end: date
    validation_end: date

    def summary(self) -> dict[str, object]:
        def describe(split: LagSplit) -> dict[str, object]:
            lags = [row.lag_days for row in split.rows if row.recovered]
            return {
                "rows": len(split),
                "recovered_rows": len(lags),
                "mean_lag_days": round(sum(lags) / len(lags), 2) if lags else None,
                "first_flag_date": min((r.flagged_date for r in split.rows), default=None),
                "last_flag_date": max((r.flagged_date for r in split.rows), default=None),
            }

        out: dict[str, object] = {
            "horizon_days": self.horizon_days,
            "seed": self.seed,
            "train_end": self.train_end.isoformat(),
            "validation_end": self.validation_end.isoformat(),
        }
        out["splits"] = {s.name: describe(s) for s in (self.train, self.validation, self.test)}
        return out


def _segment_for(customer: Customer) -> str:
    history = max(customer.invoice_count, 1)
    return segment_id(
        on_time_ratio_90d=customer.on_time_ratio_90d,
        avg_days_late=customer.avg_days_late,
        broken_promise_rate=min(customer.prior_broken_promises_count / history, 1.0),
        dispute_rate=min(customer.prior_disputes_count / history, 1.0),
    )


def observe_lags(
    invoices: list[Invoice],
    customers: list[Customer],
) -> list[LagObservation]:
    """Build lag observations from mature invoices.

    Recovered invoices contribute (segment, lag) rows. Mature but unrecovered
    invoices contribute rows with ``recovered=False`` and no lag -- they carry
    no timing information, but the backtest needs them to score the
    probability input honestly (a forecast that only ever sees payers is
    validated on a lie).
    """

    by_id = {customer.customer_id: customer for customer in customers}
    rows: list[LagObservation] = []
    for invoice in invoices:
        customer = by_id.get(invoice.customer_id)
        if customer is None:
            raise KeyError(f"No customer record for {invoice.customer_id}")
        lag: int | None = None
        if invoice.recovered and invoice.recovered_date is not None:
            lag = (invoice.recovered_date - invoice.flagged_date).days
        rows.append(
            LagObservation(
                invoice_id=invoice.invoice_id,
                segment=_segment_for(customer),
                lag_days=lag if lag is not None else -1,
                amount=invoice.amount,
                flagged_date=invoice.flagged_date,
                recovered=bool(invoice.recovered),
            )
        )
    return rows


def build_lag_dataset(
    batch: SyntheticBatch,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> LagDataset:
    """Turn a generated batch into temporally split lag observations."""

    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    if not 0.0 <= validation_fraction < 1.0:
        raise ValueError("validation_fraction must be in [0, 1)")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train and validation fractions must leave room for a test split")

    mature = batch.mature_invoices()
    if not mature:
        raise ValueError(
            "no invoices have a mature label; generate with a longer timeline_days "
            "than horizon_days"
        )

    rows = observe_lags(mature, batch.customers)
    ordered = sorted(rows, key=lambda row: (row.flagged_date, row.invoice_id))
    flag_dates = [row.flagged_date for row in ordered]

    # Cut on dates, not row indices: every invoice flagged on a given day
    # lands in the same split.
    train_end = flag_dates[min(int(len(ordered) * train_fraction), len(ordered) - 1)]
    validation_index = min(
        int(len(ordered) * (train_fraction + validation_fraction)), len(ordered) - 1
    )
    validation_end = flag_dates[validation_index]
    if validation_end <= train_end:
        validation_end = train_end

    buckets: dict[str, list[LagObservation]] = {"train": [], "validation": [], "test": []}
    for row in ordered:
        if row.flagged_date < train_end:
            buckets["train"].append(row)
        elif row.flagged_date < validation_end:
            buckets["validation"].append(row)
        else:
            buckets["test"].append(row)

    for name, bucket in buckets.items():
        if not bucket:
            raise ValueError(
                f"the {name} split is empty; widen timeline_days or increase batch_size"
            )

    return LagDataset(
        train=LagSplit("train", tuple(buckets["train"])),
        validation=LagSplit("validation", tuple(buckets["validation"])),
        test=LagSplit("test", tuple(buckets["test"])),
        horizon_days=batch.horizon_days,
        seed=batch.seed,
        train_end=train_end,
        validation_end=validation_end,
    )
