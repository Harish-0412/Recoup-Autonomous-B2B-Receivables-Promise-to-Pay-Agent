"""Turn generated records into leakage-safe frames on disk.

Two rules are enforced here rather than remembered:

1. **The hidden archetype never leaves the generator.** It is stripped from
   every frame and asserted absent before anything is written. Only aggregate
   archetype *counts* reach the manifest, as provenance metadata.
2. **Labels live in their own frame.** ``recovered`` / ``recovered_date`` are
   written to ``labels.csv`` and are never merged into the frame that the
   Phase 5 feature function reads. Joining them is a deliberate act in a
   training script, not something that can happen by accident.
"""

from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Sequence

import json
import pandas as pd

from src.data.synthetic_generator import (
    GENERATOR_VERSION,
    HIDDEN_FIELDS,
    ARCHETYPE_PROFILES,
    Customer,
    CustomerArchetype,
    Invoice,
    ReplySeedExample,
)

#: Ground-truth columns. Never allowed in a training frame.
LABEL_COLUMNS: tuple[str, ...] = ("recovered", "recovered_date")

#: Columns forbidden in any training-facing frame, for any reason.
FORBIDDEN_IN_TRAINING: tuple[str, ...] = tuple(HIDDEN_FIELDS) + LABEL_COLUMNS

_CUSTOMER_ATTRIBUTES: tuple[str, ...] = (
    "industry",
    "preferred_channel",
    "tenure_months",
    "invoice_count",
    "avg_invoice_amount",
    "on_time_ratio_90d",
    "on_time_ratio_all_time",
    "avg_days_late",
    "prior_broken_promises_count",
    "prior_disputes_count",
)

_INVOICE_ATTRIBUTES: tuple[str, ...] = (
    "amount",
    "currency",
    "issue_date",
    "due_date",
    "payment_terms_days",
    "flagged_date",
    "days_overdue_at_flag",
    "current_escalation_tier",
    "prior_reminders_sent",
    "days_since_last_contact",
    "has_prior_promise",
    "prior_promise_kept",
)

CUSTOMER_EXPORT_COLUMNS: tuple[str, ...] = (
    "customer_id",
    "name",
) + _CUSTOMER_ATTRIBUTES

INVOICE_EXPORT_COLUMNS: tuple[str, ...] = (
    "invoice_id",
    "customer_id",
) + _INVOICE_ATTRIBUTES + LABEL_COLUMNS

#: One row per invoice, customer history joined on and prefixed ``customer_``
#: so it lines up with the Phase 5 feature names.
TRAINING_FRAME_COLUMNS: tuple[str, ...] = (
    ("invoice_id", "customer_id", "as_of")
    + tuple(f"invoice_{name}" for name in _INVOICE_ATTRIBUTES)
    + tuple(f"customer_{name}" for name in _CUSTOMER_ATTRIBUTES)
)


class LeakageError(RuntimeError):
    """Raised when a forbidden column reaches a training-facing frame."""


def _assert_no_leakage(frame: pd.DataFrame, *, context: str) -> None:
    leaked = sorted(set(frame.columns) & set(FORBIDDEN_IN_TRAINING))
    if leaked:
        raise LeakageError(f"{context} must not contain {leaked}")


def _index_customers(customers: Iterable[Customer]) -> dict[str, Customer]:
    return {customer.customer_id: customer for customer in customers}


def to_customers_frame(customers: Sequence[Customer]) -> pd.DataFrame:
    """Customer records with the hidden archetype stripped."""

    rows = [
        {column: getattr(customer, column) for column in CUSTOMER_EXPORT_COLUMNS}
        for customer in customers
    ]
    frame = pd.DataFrame(rows, columns=list(CUSTOMER_EXPORT_COLUMNS))
    _assert_no_leakage(frame, context="customers frame")
    return frame


def to_invoices_frame(invoices: Sequence[Invoice]) -> pd.DataFrame:
    """Raw invoice records including outcomes.

    This is the inspection dump, not a training input -- it deliberately
    carries the label columns so a human can read the ground truth.
    """

    rows = [
        {column: getattr(invoice, column) for column in INVOICE_EXPORT_COLUMNS}
        for invoice in invoices
    ]
    return pd.DataFrame(rows, columns=list(INVOICE_EXPORT_COLUMNS))


def to_training_frame(
    customers: Sequence[Customer],
    invoices: Sequence[Invoice],
) -> pd.DataFrame:
    """Join customer history onto each invoice, with no archetype and no labels.

    ``as_of`` is the invoice's flag date: the instant every feature must be
    computed as of, both in training and at inference time.
    """

    by_id = _index_customers(customers)

    rows: list[dict[str, Any]] = []
    for invoice in invoices:
        customer = by_id.get(invoice.customer_id)
        if customer is None:
            raise KeyError(f"No customer record for {invoice.customer_id}")

        row: dict[str, Any] = {
            "invoice_id": invoice.invoice_id,
            "customer_id": invoice.customer_id,
            "as_of": invoice.flagged_date,
        }
        for name in _INVOICE_ATTRIBUTES:
            row[f"invoice_{name}"] = getattr(invoice, name)
        for name in _CUSTOMER_ATTRIBUTES:
            row[f"customer_{name}"] = getattr(customer, name)
        rows.append(row)

    frame = pd.DataFrame(rows, columns=list(TRAINING_FRAME_COLUMNS))
    _assert_no_leakage(frame, context="training frame")
    return frame


def to_labels_frame(invoices: Sequence[Invoice], *, horizon_days: int = 30) -> pd.DataFrame:
    """Ground truth, kept in its own frame and joined on ``invoice_id``."""

    rows = [
        {
            "invoice_id": invoice.invoice_id,
            "recovered": invoice.recovered,
            "recovered_date": invoice.recovered_date,
            "horizon_days": horizon_days,
        }
        for invoice in invoices
    ]
    return pd.DataFrame(rows, columns=["invoice_id", "recovered", "recovered_date", "horizon_days"])


def to_reply_records(examples: Sequence[ReplySeedExample]) -> list[dict[str, Any]]:
    """Reply seed examples as JSON-ready dicts."""

    return [json.loads(example.model_dump_json()) for example in examples]


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a CSV that is byte-identical across runs and platforms."""

    frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def export_batch(
    customers: Sequence[Customer],
    invoices: Sequence[Invoice],
    out_dir: Path,
    *,
    seed: int,
    horizon_days: int = 30,
    as_of: date | None = None,
    generator_version: str = GENERATOR_VERSION,
    reply_seed_examples: Sequence[ReplySeedExample] | None = None,
) -> Path:
    """Write the full export set and return the manifest path.

    Files written:

    ``customers.csv``
        Customer records, archetype stripped.
    ``invoices.csv``
        Invoice records including ground-truth outcomes, for inspection.
    ``training_frame.csv``
        One row per invoice, features only -- no archetype, no labels.
    ``labels.csv``
        ``invoice_id`` -> ``recovered`` / ``recovered_date``.
    ``reply_seed_examples.json``
        Label-first reply examples for the Phase 3 sanity tests.
    ``manifest.json``
        Seed, generator version, row counts, archetype distribution, and a
        SHA-256 of every file written -- so "same seed, same data" is a
        checkable claim rather than an assertion.
    """

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    customers_frame = to_customers_frame(customers)
    invoices_frame = to_invoices_frame(invoices)
    training_frame = to_training_frame(customers, invoices)
    labels_frame = to_labels_frame(invoices, horizon_days=horizon_days)

    _write_csv(customers_frame, out_dir / "customers.csv")
    _write_csv(invoices_frame, out_dir / "invoices.csv")
    _write_csv(training_frame, out_dir / "training_frame.csv")
    _write_csv(labels_frame, out_dir / "labels.csv")

    replies = list(reply_seed_examples or [])
    (out_dir / "reply_seed_examples.json").write_text(
        json.dumps(to_reply_records(replies), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    archetype_counts = {archetype.value: 0 for archetype in CustomerArchetype}
    for customer in customers:
        archetype_counts[customer.archetype.value] += 1

    recovered_count = sum(1 for invoice in invoices if invoice.recovered)
    data_files = [
        "customers.csv",
        "invoices.csv",
        "training_frame.csv",
        "labels.csv",
        "reply_seed_examples.json",
    ]

    manifest: dict[str, Any] = {
        "generator_version": generator_version,
        "seed": seed,
        "horizon_days": horizon_days,
        "as_of": (as_of or (invoices[0].flagged_date if invoices else None)),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "row_counts": {
            "customers": len(customers_frame),
            "invoices": len(invoices_frame),
            "training_frame": len(training_frame),
            "labels": len(labels_frame),
            "reply_seed_examples": len(replies),
        },
        "archetype_counts": archetype_counts,
        "archetype_mix_configured": {
            archetype.value: ARCHETYPE_PROFILES[archetype].recovery_logit_base
            for archetype in CustomerArchetype
        },
        "recovery_rate": round(recovered_count / len(invoices), 6) if invoices else None,
        "training_frame_columns": list(TRAINING_FRAME_COLUMNS),
        "label_columns": list(LABEL_COLUMNS),
        "excluded_from_training": list(FORBIDDEN_IN_TRAINING),
        "content_sha256": {name: _sha256(out_dir / name) for name in data_files},
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest_path
