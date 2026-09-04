"""Statistical Synthetic Data Generation using SDV (Synthetic Data Vault).

Upgrades the hand-rolled archetype generator with MIT Data-to-AI Lab's SDV library:
- Models multivariate joint distributions using GaussianCopulaSynthesizer.
- Captures correlations between customer payment history, overdue duration, and recovery outcomes.
- Evaluates statistical fidelity and privacy using SDMetrics QualityReport.
- Converts sampled synthetic data directly into domain ``GeneratedBatch`` instances.
"""

from __future__ import annotations

import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sdmetrics.reports.single_table import QualityReport
from sdv.metadata import SingleTableMetadata
from sdv.single_table import GaussianCopulaSynthesizer

from src.data.synthetic_generator import (
    Customer,
    CustomerArchetype,
    Invoice,
    SyntheticBatch,
)

SDV_GENERATOR_VERSION = "sdv-gaussian-copula-v1"


def batch_to_dataframe(batch: SyntheticBatch) -> pd.DataFrame:
    """Flatten a SyntheticBatch into a single tabular training DataFrame."""
    customers = {c.customer_id: c for c in batch.customers}
    rows = []
    for inv in batch.invoices:
        cust = customers.get(inv.customer_id)
        if not cust:
            continue
        rows.append(
            {
                "invoice_id": inv.invoice_id,
                "customer_id": inv.customer_id,
                "amount": float(inv.amount),
                "days_overdue": int(inv.days_overdue_at_flag),
                "payment_terms_days": int(inv.payment_terms_days),
                "current_escalation_tier": int(inv.current_escalation_tier),
                "prior_reminders_sent": int(inv.prior_reminders_sent),
                "days_since_last_contact": int(inv.days_since_last_contact),
                "has_prior_promise": bool(inv.has_prior_promise),
                "industry": cust.industry,
                "preferred_channel": cust.preferred_channel,
                "tenure_months": int(cust.tenure_months),
                "invoice_count": int(cust.invoice_count),
                "avg_invoice_amount": float(cust.avg_invoice_amount),
                "on_time_ratio_90d": float(cust.on_time_ratio_90d),
                "on_time_ratio_all_time": float(cust.on_time_ratio_all_time),
                "avg_days_late": float(cust.avg_days_late),
                "prior_broken_promises_count": int(cust.prior_broken_promises_count),
                "prior_disputes_count": int(cust.prior_disputes_count),
                "recovered": bool(inv.recovered) if inv.recovered is not None else False,
            }
        )
    return pd.DataFrame(rows)


def build_sdv_metadata(df: pd.DataFrame) -> SingleTableMetadata:
    """Construct SDV SingleTableMetadata with proper column roles and data types."""
    metadata = SingleTableMetadata()
    metadata.detect_from_dataframe(df)

    metadata.set_primary_key(column_name="invoice_id")
    metadata.update_column(column_name="customer_id", sdtype="categorical")
    metadata.update_column(column_name="industry", sdtype="categorical")
    metadata.update_column(column_name="preferred_channel", sdtype="categorical")
    metadata.update_column(column_name="has_prior_promise", sdtype="boolean")
    metadata.update_column(column_name="recovered", sdtype="boolean")

    return metadata


class RecoupSDVSynthesizer:
    """Wrapper around SDV GaussianCopulaSynthesizer for Recoup receivable data."""

    def __init__(self, metadata: SingleTableMetadata | None = None) -> None:
        self.metadata = metadata
        self.synthesizer: GaussianCopulaSynthesizer | None = None

    def fit(self, data: pd.DataFrame | SyntheticBatch) -> RecoupSDVSynthesizer:
        """Fit the SDV copula synthesizer on tabular data or a SyntheticBatch."""
        df = batch_to_dataframe(data) if isinstance(data, SyntheticBatch) else data.copy()
        if self.metadata is None:
            self.metadata = build_sdv_metadata(df)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.synthesizer = GaussianCopulaSynthesizer(self.metadata)
            self.synthesizer.fit(df)

        return self

    def sample(self, num_rows: int) -> pd.DataFrame:
        """Sample synthetic tabular cases from the fitted synthesizer."""
        if self.synthesizer is None:
            raise RuntimeError("Synthesizer must be fitted before sampling.")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            synthetic = self.synthesizer.sample(num_rows=num_rows)

        # Apply post-sampling domain bounds
        synthetic["amount"] = synthetic["amount"].clip(lower=1000.0)
        synthetic["days_overdue"] = synthetic["days_overdue"].clip(lower=0).round().astype(int)
        synthetic["current_escalation_tier"] = (
            synthetic["current_escalation_tier"].clip(lower=0, upper=3).round().astype(int)
        )
        synthetic["on_time_ratio_90d"] = synthetic["on_time_ratio_90d"].clip(0.0, 1.0)
        synthetic["on_time_ratio_all_time"] = synthetic["on_time_ratio_all_time"].clip(0.0, 1.0)
        synthetic["avg_days_late"] = synthetic["avg_days_late"].clip(lower=0.0)
        synthetic["prior_reminders_sent"] = (
            synthetic["prior_reminders_sent"].clip(lower=0).round().astype(int)
        )
        synthetic["days_since_last_contact"] = (
            synthetic["days_since_last_contact"].clip(lower=0).round().astype(int)
        )
        return synthetic

    def sample_to_batch(
        self,
        num_rows: int,
        as_of: date | None = None,
        horizon_days: int = 30,
    ) -> SyntheticBatch:
        """Sample synthetic cases and construct a typed SyntheticBatch."""
        df = self.sample(num_rows)
        ref_date = as_of or date.today()

        customers_map: dict[str, Customer] = {}
        invoices: list[Invoice] = []

        for idx, row in df.iterrows():
            cust_id = str(row["customer_id"])
            if cust_id not in customers_map:
                customers_map[cust_id] = Customer(
                    customer_id=cust_id,
                    name=f"Synthesized Corp {cust_id[-6:]}",
                    industry=str(row["industry"]),
                    preferred_channel=str(row["preferred_channel"]),
                    tenure_months=max(1, int(row["tenure_months"])),
                    invoice_count=max(1, int(row["invoice_count"])),
                    avg_invoice_amount=max(1000.0, float(row["avg_invoice_amount"])),
                    on_time_ratio_90d=float(row["on_time_ratio_90d"]),
                    on_time_ratio_all_time=float(row["on_time_ratio_all_time"]),
                    avg_days_late=float(row["avg_days_late"]),
                    prior_broken_promises_count=max(0, int(row["prior_broken_promises_count"])),
                    prior_disputes_count=max(0, int(row["prior_disputes_count"])),
                    archetype=CustomerArchetype.RELIABLE,
                )

            inv_id = f"INV-SDV-{idx:05d}"
            terms = int(row["payment_terms_days"])
            due_date = ref_date - timedelta(days=int(row["days_overdue"]))
            issue_date = due_date - timedelta(days=terms)

            invoices.append(
                Invoice(
                    invoice_id=inv_id,
                    customer_id=cust_id,
                    amount=float(row["amount"]),
                    currency="INR",
                    issue_date=issue_date,
                    due_date=due_date,
                    payment_terms_days=terms,
                    flagged_date=ref_date,
                    days_overdue_at_flag=int(row["days_overdue"]),
                    current_escalation_tier=int(row["current_escalation_tier"]),
                    prior_reminders_sent=int(row["prior_reminders_sent"]),
                    days_since_last_contact=int(row["days_since_last_contact"]),
                    has_prior_promise=bool(row["has_prior_promise"]),
                    recovered=bool(row["recovered"]),
                    recovered_date=ref_date + timedelta(days=14)
                    if bool(row["recovered"])
                    else None,
                )
            )

        return SyntheticBatch(
            generator_version=SDV_GENERATOR_VERSION,
            seed=0,
            horizon_days=horizon_days,
            as_of=ref_date,
            timeline_days=365,
            customers=list(customers_map.values()),
            invoices=invoices,
            reply_seed_examples=[],
        )

    def evaluate_quality(
        self,
        real_data: pd.DataFrame,
        synthetic_data: pd.DataFrame,
    ) -> dict[str, Any]:
        """Evaluate synthetic data fidelity using SDMetrics QualityReport."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            report = QualityReport()
            report.generate(
                real_data,
                synthetic_data,
                self.metadata.to_dict() if self.metadata else {},
                verbose=False,
            )
            score = report.get_score()
            properties = report.get_properties()

        column_shapes = 0.0
        column_trends = 0.0
        for _, prop in properties.iterrows():
            prop_name = str(prop.get("Property", ""))
            prop_score = float(prop.get("Score", 0.0))
            if "Column Shapes" in prop_name:
                column_shapes = prop_score
            elif "Column Pair Trends" in prop_name:
                column_trends = prop_score

        return {
            "quality_score": float(score),
            "column_shapes_score": column_shapes,
            "column_pair_trends_score": column_trends,
        }

    def save(self, filepath: str | Path) -> None:
        """Save the fitted SDV synthesizer to a file."""
        if self.synthesizer is None:
            raise RuntimeError("Cannot save an unfitted synthesizer.")
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.synthesizer.save(str(path))

    @classmethod
    def load(cls, filepath: str | Path) -> RecoupSDVSynthesizer:
        """Load an SDV synthesizer from a saved file."""
        synthesizer = GaussianCopulaSynthesizer.load(str(filepath))
        instance = cls(metadata=synthesizer.metadata)
        instance.synthesizer = synthesizer
        return instance
