"""Assemble the recovery training set, split by time rather than at random.

Two disciplines are enforced here, and both change the headline number:

**The split is temporal.** Train on invoices flagged early in the timeline,
validate on the middle, test on the most recent. A random split lets the model
learn from invoices flagged *after* the ones it is tested on, which is a future
it will never have at inference time. Splitting by flag date mirrors how the
model is actually used: predicting forward from what is known so far.

**Only mature labels are used.** An invoice flagged ten days before the data
was cut has no observed 30-day outcome. Including it would label a perfectly
ordinary invoice "not recovered" purely because the window had not closed,
teaching the model that recent invoices fail.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.core.domain import CaseSnapshot, CustomerSnapshot, InvoiceSnapshot, snapshot_from_generated
from src.data.synthetic_generator import Customer, Invoice, SyntheticBatch
from src.ml.features.recovery_features import FEATURE_COLUMNS_V1, build_recovery_features

LABEL_COLUMN = "recovered_within_horizon"


@dataclass(frozen=True)
class LabeledRecord:
    """One point-in-time training row from any label source.

    ``features`` must carry exactly ``FEATURE_COLUMNS_V1`` and be valid as of
    ``scored_at``: synthetic rows compute them from the generator, warehouse
    rows read them frozen from ``scored`` decision traces. ``scored_at`` is the
    evaluation instant the 30-day label window opens at -- never a due date.
    """

    invoice_id: str
    customer_id: str
    scored_at: date
    features: dict[str, float]
    label: int
    amount: float


@dataclass(frozen=True)
class RecoverySplit:
    """One temporal slice: features, labels, and the cases behind them."""

    name: str
    features: pd.DataFrame
    labels: np.ndarray
    cases: list[CaseSnapshot]
    flag_dates: list[date]

    def __len__(self) -> int:
        return len(self.labels)

    @property
    def positive_rate(self) -> float:
        return float(self.labels.mean()) if len(self.labels) else 0.0


@dataclass(frozen=True)
class RecoveryDataset:
    """Train / validation / test slices plus the provenance to reproduce them."""

    train: RecoverySplit
    validation: RecoverySplit
    test: RecoverySplit
    feature_columns: tuple[str, ...]
    horizon_days: int
    seed: int
    train_end: date
    validation_end: date

    def summary(self) -> dict[str, object]:
        return {
            "feature_count": len(self.feature_columns),
            "horizon_days": self.horizon_days,
            "seed": self.seed,
            "train_end": self.train_end.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "splits": {
                split.name: {
                    "rows": len(split),
                    "positive_rate": round(split.positive_rate, 4),
                    "first_flag_date": min(split.flag_dates).isoformat() if split else None,
                    "last_flag_date": max(split.flag_dates).isoformat() if split else None,
                }
                for split in (self.train, self.validation, self.test)
            },
        }


def build_cases(
    invoices: Sequence[Invoice],
    customers: Sequence[Customer],
) -> list[CaseSnapshot]:
    """Adapt generated records into the snapshots the whole system scores on.

    Routing through ``snapshot_from_generated`` rather than reading the
    generator objects directly is what guarantees the training path sees
    exactly the fields the serving path sees -- the adapter refuses to carry
    ground truth, so leakage would have to be written deliberately.
    """

    lookup = {customer.customer_id: customer for customer in customers}
    cases: list[CaseSnapshot] = []
    for invoice in invoices:
        customer = lookup.get(invoice.customer_id)
        if customer is None:
            raise KeyError(f"No customer record for {invoice.customer_id}")
        cases.append(snapshot_from_generated(invoice, customer))
    return cases


def features_frame(cases: Sequence[CaseSnapshot]) -> pd.DataFrame:
    """Feature matrix for a list of cases, columns in the declared order."""

    rows = [build_recovery_features(case) for case in cases]
    frame = pd.DataFrame(rows, columns=list(FEATURE_COLUMNS_V1))
    return frame.astype("float64")


def records_from_synthetic(batch: SyntheticBatch) -> list[LabeledRecord]:
    """Adapt a generated batch into source-agnostic training rows."""

    mature = batch.mature_invoices()
    cases = build_cases(mature, batch.customers)
    records: list[LabeledRecord] = []
    for invoice, case in zip(mature, cases, strict=True):
        records.append(
            LabeledRecord(
                invoice_id=invoice.invoice_id,
                customer_id=invoice.customer_id,
                scored_at=invoice.flagged_date,
                features=build_recovery_features(case),
                label=1 if invoice.recovered else 0,
                amount=float(invoice.amount),
            )
        )
    return records


def cases_from_records(records: Sequence[LabeledRecord]) -> list[CaseSnapshot]:
    """Rebuild evaluation snapshots from stored point-in-time features.

    Used only to run the rules-based incumbent over warehouse holdouts: every
    field the rules scorer reads is present in the frozen vector, with dates
    re-anchored at ``scored_at``. These snapshots never train anything.
    """

    cases: list[CaseSnapshot] = []
    for record in records:
        feats = record.features
        due_date = record.scored_at - timedelta(days=int(feats["days_overdue_at_scoring"]))
        issue_date = record.scored_at - timedelta(days=int(feats["invoice_age_days"]))
        cases.append(
            CaseSnapshot(
                invoice=InvoiceSnapshot(
                    invoice_id=record.invoice_id,
                    customer_id=record.customer_id,
                    amount=max(record.amount, 1.0),
                    issue_date=issue_date,
                    due_date=due_date,
                    payment_terms_days=max(int(feats["payment_terms_days"]), 1),
                    as_of=record.scored_at,
                    days_overdue=int(feats["days_overdue_at_scoring"]),
                    ladder_index=int(feats["current_escalation_tier"]),
                    prior_reminders_sent=int(feats["prior_reminders_sent"]),
                    days_since_last_contact=int(feats["days_since_last_contact"]),
                    has_prior_promise=bool(feats["has_prior_promise"]),
                    prior_promise_kept=(
                        None
                        if not feats["has_prior_promise"]
                        else bool(feats["prior_promise_kept"] > 0.5)
                    ),
                ),
                customer=CustomerSnapshot(
                    customer_id=record.customer_id,
                    tenure_months=int(feats["customer_tenure_months"]),
                    invoice_count=int(feats["customer_invoice_count"]),
                    avg_invoice_amount=float(feats["invoice_amount"])
                    / max(float(feats["invoice_amount_vs_customer_avg_ratio"]), 1e-6),
                    on_time_ratio_90d=float(feats["customer_on_time_ratio_90d"]),
                    on_time_ratio_all_time=float(feats["customer_on_time_ratio_all_time"]),
                    avg_days_late=float(feats["customer_avg_days_late"]),
                    prior_broken_promises_count=int(feats["customer_broken_promises_count"]),
                    prior_disputes_count=int(feats["customer_dispute_count"]),
                ),
            )
        )
    return cases


def build_dataset_from_records(
    records: Sequence[LabeledRecord],
    *,
    horizon_days: int,
    seed: int,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> RecoveryDataset:
    """Temporal split over any label source, cut on scored dates not indices."""

    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be in (0, 1)")
    if not 0.0 <= validation_fraction < 1.0:
        raise ValueError("validation_fraction must be in [0, 1)")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train and validation fractions must leave room for a test split")
    if not records:
        raise ValueError("no labelled rows; run the ETL (or generator) first")

    ordered = sorted(records, key=lambda record: (record.scored_at, record.invoice_id))
    flag_dates = [record.scored_at for record in ordered]

    # Cut on dates, not row indices, so every invoice scored on a given day
    # lands in the same split. Splitting mid-day would leak same-day
    # information across the boundary.
    train_end = flag_dates[min(int(len(ordered) * train_fraction), len(ordered) - 1)]
    validation_index = min(
        int(len(ordered) * (train_fraction + validation_fraction)), len(ordered) - 1
    )
    validation_end = flag_dates[validation_index]
    if validation_end <= train_end:
        validation_end = train_end

    buckets: dict[str, list[LabeledRecord]] = {"train": [], "validation": [], "test": []}
    for record in ordered:
        if record.scored_at < train_end:
            buckets["train"].append(record)
        elif record.scored_at < validation_end:
            buckets["validation"].append(record)
        else:
            buckets["test"].append(record)

    for name, bucket in buckets.items():
        if not bucket:
            raise ValueError(f"the {name} split is empty; widen the scored window or add rows")

    splits: dict[str, RecoverySplit] = {}
    for name, bucket in buckets.items():
        splits[name] = RecoverySplit(
            name=name,
            features=pd.DataFrame(
                [record.features for record in bucket],
                columns=list(FEATURE_COLUMNS_V1),
            ).astype("float64"),
            labels=np.array([record.label for record in bucket], dtype=int),
            cases=cases_from_records(bucket),
            flag_dates=[record.scored_at for record in bucket],
        )

    return RecoveryDataset(
        train=splits["train"],
        validation=splits["validation"],
        test=splits["test"],
        feature_columns=FEATURE_COLUMNS_V1,
        horizon_days=horizon_days,
        seed=seed,
        train_end=train_end,
        validation_end=validation_end,
    )


def build_dataset(
    batch: SyntheticBatch,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> RecoveryDataset:
    """Turn a generated batch into temporally split, leakage-safe slices."""

    mature = batch.mature_invoices()
    if not mature:
        raise ValueError(
            "no invoices have a mature label; generate with a longer timeline_days "
            "than horizon_days"
        )
    return build_dataset_from_records(
        records_from_synthetic(batch),
        horizon_days=batch.horizon_days,
        seed=batch.seed,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
    )
