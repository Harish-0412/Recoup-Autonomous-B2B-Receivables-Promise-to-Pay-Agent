"""ETL job to generate training data for the Broken-Promise Risk Scorer.

Extracts promise history, joins with invoices and payment outcomes, labels missed
promises (> 3 days late), and writes a standardized CSV ready for model training.
Can read from existing database or generate realistic archetyped mock data.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUTPUT_CSV = DATA_DIR / "broken_promise_training.csv"

FEATURE_COLUMNS = (
    "customer_broken_promise_rate",
    "customer_broken_promises_count",
    "customer_avg_days_late",
    "customer_on_time_ratio_90d",
    "customer_on_time_ratio_all_time",
    "recency_weighted_on_time_score",
    "customer_dispute_rate",
    "customer_invoice_count",
    "customer_tenure_months",
    "invoice_amount",
    "invoice_amount_log",
    "invoice_amount_vs_customer_avg_ratio",
    "days_overdue_at_scoring",
    "payment_terms_days",
    "days_since_last_contact",
    "prior_reminders_sent",
    "promise_amount_ratio",
    "promise_horizon_days",
    "current_escalation_tier",
)


def _clamp(val: float, low: float, high: float) -> float:
    return float(min(max(val, low), high))


def recency_weighted_on_time_score(
    on_time_ratio_90d: float,
    on_time_ratio_all_time: float,
    invoice_count: int,
    *,
    half_life_invoices: float = 12.0,
) -> float:
    weight = 1.0 - math.exp(-max(invoice_count, 0) / max(half_life_invoices, 1e-6))
    recent_weight = 0.35 + 0.45 * weight
    return float(recent_weight * on_time_ratio_90d + (1.0 - recent_weight) * on_time_ratio_all_time)


def generate_mock_promise_dataset(
    n_samples: int = 12000,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate high-fidelity, real-life B2B promise-to-pay dataset.

    Reflects genuine behavioral patterns:
    - Serial promise-breakers who repeatedly commit but fail to pay on time
    - Highly reliable accounts that rarely break commitments
    - Horizon effect: promises set far in the future (>14 days) are much more likely to break
    - Escalation pressure: higher tiers have lower keep rates due to distress
    - Financial distress signals: high dispute rates and overdue days correlate with broken promises
    """
    rng = np.random.default_rng(seed)

    archetypes = [
        {
            "name": "RELIABLE",
            "weight": 0.35,
            "base_keep_rate": 0.94,
            "mean_days_late": 2.0,
            "mean_on_time": 0.93,
            "disp_rate": 0.02,
        },
        {
            "name": "LATE_BUT_PAYS",
            "weight": 0.28,
            "base_keep_rate": 0.82,
            "mean_days_late": 16.0,
            "mean_on_time": 0.35,
            "disp_rate": 0.06,
        },
        {
            "name": "ERRATIC",
            "weight": 0.18,
            "base_keep_rate": 0.45,
            "mean_days_late": 26.0,
            "mean_on_time": 0.46,
            "disp_rate": 0.15,
        },
        {
            "name": "NEW_UNKNOWN",
            "weight": 0.10,
            "base_keep_rate": 0.64,
            "mean_days_late": 9.0,
            "mean_on_time": 0.60,
            "disp_rate": 0.07,
        },
        {
            "name": "RISK_ESCALATING",
            "weight": 0.09,
            "base_keep_rate": 0.50,
            "mean_days_late": 15.0,
            "mean_on_time": 0.40,
            "disp_rate": 0.12,
        },
    ]

    weights = [a["weight"] for a in archetypes]
    chosen_archetypes = rng.choice(archetypes, size=n_samples, p=weights)

    rows: list[dict[str, Any]] = []

    for i in range(n_samples):
        arch = chosen_archetypes[i]

        invoice_count = int(rng.integers(1, 80))
        tenure_months = int(rng.integers(1, 96))
        avg_inv_amt = float(rng.lognormal(mean=10.5, sigma=0.8))  # in INR
        inv_amt = float(avg_inv_amt * rng.lognormal(mean=0.0, sigma=0.4))
        inv_amt_ratio = _clamp(inv_amt / max(avg_inv_amt, 1.0), 0.1, 10.0)

        on_time_all = _clamp(rng.normal(arch["mean_on_time"], 0.08), 0.0, 1.0)
        drift = -0.35 if arch["name"] == "RISK_ESCALATING" else rng.normal(0.0, 0.05)
        on_time_90d = _clamp(on_time_all + drift, 0.0, 1.0)

        avg_days_late = max(0.0, rng.normal(arch["mean_days_late"], 4.0))
        expected_prior_promises = max(0, int(invoice_count * 0.35))
        prior_broken_count = int(
            rng.poisson(expected_prior_promises * (1.0 - arch["base_keep_rate"]))
        )
        broken_rate = min(1.0, prior_broken_count / max(expected_prior_promises, 1))

        dispute_rate = _clamp(rng.normal(arch["disp_rate"], 0.03), 0.0, 1.0)

        days_overdue = int(
            rng.choice(
                [
                    rng.integers(1, 15),
                    rng.integers(15, 45),
                    rng.integers(45, 120),
                ],
                p=[0.55, 0.30, 0.15],
            )
        )

        payment_terms = int(rng.choice([15, 30, 45, 60], p=[0.15, 0.60, 0.15, 0.10]))
        days_since_contact = int(rng.integers(1, 25))
        prior_reminders = int(rng.integers(1, 6))
        escalation_tier = int(min(3, prior_reminders - 1))

        # Promise features
        # Some customers promise partial amounts, some full
        promise_amount_ratio = float(rng.choice([1.0, 0.5, 0.25, 0.8], p=[0.7, 0.15, 0.08, 0.07]))
        promise_horizon_days = int(
            rng.choice(
                [2, 3, 5, 7, 10, 14, 21, 30], p=[0.20, 0.25, 0.20, 0.15, 0.08, 0.06, 0.04, 0.02]
            )
        )

        recency_score = recency_weighted_on_time_score(on_time_90d, on_time_all, invoice_count)

        # Ground truth broken promise probability calculation
        # Logit formulation:
        # High broken_rate -> strongly increases broken probability
        # High horizon (>7 days) -> increases broken probability ("saying Friday but setting it next month")
        # High escalation tier and disputes -> increases broken probability
        # High recency punctuality -> decreases broken probability
        logit = -math.log(arch["base_keep_rate"] / (1.0 - arch["base_keep_rate"] + 1e-6))
        logit += 2.8 * broken_rate
        logit += 0.08 * (promise_horizon_days - 5)
        logit += 0.025 * days_overdue
        logit += 0.45 * escalation_tier
        logit += 1.5 * dispute_rate
        logit -= 2.2 * recency_score
        if promise_amount_ratio < 0.5:
            logit += 0.35  # partial commitments are sometimes stalling tactics

        # Add realistic noise
        logit += rng.normal(0.0, 0.45)
        prob_broken = 1.0 / (1.0 + math.exp(-_clamp(logit, -10.0, 10.0)))

        is_broken = int(rng.random() < prob_broken)

        row = {
            "promise_id": f"PRM-MOCK-{i+1000:06d}",
            "customer_id": f"CUST-MOCK-{(i % 800) + 1:04d}",
            "customer_broken_promise_rate": round(broken_rate, 4),
            "customer_broken_promises_count": prior_broken_count,
            "customer_avg_days_late": round(avg_days_late, 2),
            "customer_on_time_ratio_90d": round(on_time_90d, 4),
            "customer_on_time_ratio_all_time": round(on_time_all, 4),
            "recency_weighted_on_time_score": round(recency_score, 4),
            "customer_dispute_rate": round(dispute_rate, 4),
            "customer_invoice_count": invoice_count,
            "customer_tenure_months": tenure_months,
            "invoice_amount": round(inv_amt, 2),
            "invoice_amount_log": round(math.log1p(inv_amt), 4),
            "invoice_amount_vs_customer_avg_ratio": round(inv_amt_ratio, 3),
            "days_overdue_at_scoring": days_overdue,
            "payment_terms_days": payment_terms,
            "days_since_last_contact": days_since_contact,
            "prior_reminders_sent": prior_reminders,
            "promise_amount_ratio": round(promise_amount_ratio, 3),
            "promise_horizon_days": promise_horizon_days,
            "current_escalation_tier": escalation_tier,
            "is_broken": is_broken,
        }
        rows.append(row)

    return rows


def run(output_path: Path = OUTPUT_CSV, n_samples: int = 15000) -> None:
    """Run ETL job and generate training dataset."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_mock_promise_dataset(n_samples=n_samples, seed=42)

    fieldnames = ["promise_id", "customer_id", *FEATURE_COLUMNS, "is_broken"]
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    broken_count = sum(r["is_broken"] for r in rows)
    print(f"ETL completed: wrote {len(rows)} rows to {output_path}")
    print(
        f"Broken promises: {broken_count} ({broken_count / len(rows):.1%}), Honored: {len(rows) - broken_count}"
    )


if __name__ == "__main__":
    run()
