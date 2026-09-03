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
from datetime import date

import numpy as np
import pandas as pd

from app.core.domain import CaseSnapshot, snapshot_from_generated
from src.data.synthetic_generator import Customer, Invoice, SyntheticBatch
from src.ml.features.recovery_features import FEATURE_COLUMNS_V1, build_recovery_features

LABEL_COLUMN = "recovered_within_horizon"


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


def build_dataset(
    batch: SyntheticBatch,
    *,
    train_fraction: float = 0.70,
    validation_fraction: float = 0.15,
) -> RecoveryDataset:
    """Turn a generated batch into temporally split, leakage-safe slices."""

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

    ordered = sorted(mature, key=lambda invoice: (invoice.flagged_date, invoice.invoice_id))
    flag_dates = [invoice.flagged_date for invoice in ordered]

    # Cut on dates, not row indices, so every invoice flagged on a given day
    # lands in the same split. Splitting mid-day would leak same-day
    # information across the boundary.
    train_end = flag_dates[min(int(len(ordered) * train_fraction), len(ordered) - 1)]
    validation_index = min(
        int(len(ordered) * (train_fraction + validation_fraction)), len(ordered) - 1
    )
    validation_end = flag_dates[validation_index]
    if validation_end <= train_end:
        validation_end = train_end

    buckets: dict[str, list[Invoice]] = {"train": [], "validation": [], "test": []}
    for invoice in ordered:
        if invoice.flagged_date < train_end:
            buckets["train"].append(invoice)
        elif invoice.flagged_date < validation_end:
            buckets["validation"].append(invoice)
        else:
            buckets["test"].append(invoice)

    for name, invoices in buckets.items():
        if not invoices:
            raise ValueError(
                f"the {name} split is empty; widen timeline_days or increase batch_size"
            )

    splits: dict[str, RecoverySplit] = {}
    for name, invoices in buckets.items():
        cases = build_cases(invoices, batch.customers)
        splits[name] = RecoverySplit(
            name=name,
            features=features_frame(cases),
            labels=np.array([1 if invoice.recovered else 0 for invoice in invoices], dtype=int),
            cases=cases,
            flag_dates=[invoice.flagged_date for invoice in invoices],
        )

    return RecoveryDataset(
        train=splits["train"],
        validation=splits["validation"],
        test=splits["test"],
        feature_columns=FEATURE_COLUMNS_V1,
        horizon_days=batch.horizon_days,
        seed=batch.seed,
        train_end=train_end,
        validation_end=validation_end,
    )
