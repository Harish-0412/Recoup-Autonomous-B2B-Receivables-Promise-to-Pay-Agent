"""Load the Wave 6 warehouse outcomes into training rows.

The ETL (``scripts/etl/real_outcomes.py``) writes one parquet row per mature
``scored`` event: frozen point-in-time features plus the allocation-settled
label. This module validates that contract and adapts it to the shared
``LabeledRecord`` shape, so the trainer splits, trains and evaluates live rows
with exactly the same code as synthetic ones.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.ml.features.recovery_features import FEATURE_COLUMNS_V1
from src.ml.recovery.dataset import LabeledRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WAREHOUSE_PARQUET = REPO_ROOT / "data" / "warehouse" / "recovery_outcomes.parquet"

#: Refuse to ship a model trained on less than this many mature live rows.
#: Below it the holdout AUC is noise and the champion/challenger gate cannot
#: be trusted -- the trainer aborts instead of saving.
MIN_WAREHOUSE_ROWS = 200

REQUIRED_COLUMNS = frozenset(
    {
        "business_id",
        "invoice_id",
        "customer_id",
        "scored_at",
        "invoice_amount",
        "recovered_within_30d",
        *FEATURE_COLUMNS_V1,
    }
)


def load_warehouse_records(parquet: Path = DEFAULT_WAREHOUSE_PARQUET) -> list[LabeledRecord]:
    """Read and validate the ETL parquet into training rows."""

    import pandas as pd

    if not parquet.exists():
        raise FileNotFoundError(
            f"warehouse parquet not found at {parquet}; " "run scripts/etl/real_outcomes.py first"
        )
    frame = pd.read_parquet(parquet)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(
            f"warehouse parquet is missing columns {sorted(missing)}; "
            "re-run scripts/etl/real_outcomes.py"
        )

    records: list[LabeledRecord] = []
    for _, row in frame.iterrows():
        scored = row["scored_at"]
        scored_at = (
            scored.date() if hasattr(scored, "date") else date.fromisoformat(str(scored)[:10])
        )
        features = {column: float(row[column]) for column in FEATURE_COLUMNS_V1}
        records.append(
            LabeledRecord(
                invoice_id=str(row["invoice_id"]),
                customer_id=str(row["customer_id"]),
                scored_at=scored_at,
                features=features,
                label=int(row["recovered_within_30d"]),
                amount=float(row["invoice_amount"]),
            )
        )
    if len(records) < MIN_WAREHOUSE_ROWS:
        raise ValueError(
            f"only {len(records)} mature live rows (minimum {MIN_WAREHOUSE_ROWS}); "
            "the live book has not produced enough closed 30-day windows yet -- "
            "keep serving the rules and re-run the ETL later"
        )
    return records
