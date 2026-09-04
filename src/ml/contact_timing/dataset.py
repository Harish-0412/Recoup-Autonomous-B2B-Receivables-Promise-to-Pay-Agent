"""Engagement-log dataset for the contact-timing bandit.

One row per reminder touch: the customer's observable features, the segment
derived from them, the arm pulled under a *uniform random logging policy*, and
whether the customer responded within 48 hours. Uniform logging is what makes
the offline replay evaluation unbiased: every arm had the same propensity, so
restricting evaluation to rows where the learned policy agrees with the log
needs no propensity correction.

Provenance, stated plainly: response labels come from a behavioural response
model keyed off the generator's hidden archetype -- an assumption about inbox
habits, not measured inbox data. No public B2B reminder-response dataset
exists to train on instead. What IS real: the pipeline shape (logged touches
-> posteriors -> served suggestions -> online updates from genuine inbound
replies), the segment features (identical to production `Customer` rows), and
the evaluation protocol. The model card carries this caveat verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.synthetic_generator import CustomerArchetype, generate_batch
from src.ml.contact_timing.segments import ARMS, segment_id

#: Response propensity per archetype: (base, {daypart: delta}, {weekday: delta}).
#: Morning people pay before standup; erratic inboxes surface late in the day.
#: These are assumptions -- see the module docstring -- chosen to be separable
#: enough to learn and noisy enough to need learning.
RESPONSE_MODEL: dict[CustomerArchetype, dict] = {
    CustomerArchetype.RELIABLE: {
        "base": 0.55,
        "daypart": {"morning": 0.10, "midday": 0.02, "late": -0.06},
        "weekday": {"mon": 0.03, "fri": -0.04},
    },
    CustomerArchetype.LATE_BUT_PAYS: {
        "base": 0.45,
        "daypart": {"morning": -0.02, "midday": 0.10, "late": 0.02},
        "weekday": {"wed": 0.04, "mon": -0.05},
    },
    CustomerArchetype.ERRATIC: {
        "base": 0.30,
        "daypart": {"morning": -0.08, "midday": 0.02, "late": 0.12},
        "weekday": {"mon": -0.06, "thu": 0.05},
    },
    CustomerArchetype.NEW_UNKNOWN: {
        "base": 0.35,
        "daypart": {},
        "weekday": {},
    },
    CustomerArchetype.RISK_ESCALATING: {
        "base": 0.28,
        "daypart": {"morning": 0.06, "midday": 0.0, "late": -0.04},
        "weekday": {"tue": 0.04, "fri": -0.05},
    },
}

#: Irreducible noise on the latent propensity, mirroring the generator's own
#: honesty term: perfectly predictable labels would teach nothing.
RESPONSE_NOISE_STD = 0.03

ENGAGEMENT_COLUMNS = [
    "customer_id",
    "segment",
    "arm",
    "weekday",
    "daypart",
    "reward",
    "logging_propensity",
]


@dataclass
class EngagementLog:
    """A built log plus its build manifest."""

    frame: pd.DataFrame
    customers: int
    touches: int
    seed: int
    touches_per_customer: int


def true_response_rate(archetype: CustomerArchetype, arm: str) -> float:
    """The simulator's latent response probability for one archetype x arm."""

    day, part = arm.split("_", 1)
    spec = RESPONSE_MODEL[archetype]
    rate = (
        float(spec["base"])
        + float(spec.get("daypart", {}).get(part, 0.0))
        + float(spec.get("weekday", {}).get(day, 0.0))
    )
    return min(0.95, max(0.05, rate))


def build_engagement_log(
    *,
    customer_count: int = 900,
    touches_per_customer: int = 6,
    seed: int = 42,
    horizon_days: int = 30,
) -> EngagementLog:
    """Generate customers, then log uniform-policy touches with rewards."""

    rng = np.random.default_rng(seed)
    batch = generate_batch(
        batch_size=customer_count,
        customer_count=customer_count,
        seed=seed,
        horizon_days=horizon_days,
    )

    records: list[dict] = []
    for customer in batch.customers:
        invoice_count = max(int(customer.invoice_count), 1)
        broken_rate = min(int(customer.prior_broken_promises_count) / invoice_count, 1.0)
        dispute_rate = min(int(customer.prior_disputes_count) / invoice_count, 1.0)
        segment = segment_id(
            on_time_ratio_90d=float(customer.on_time_ratio_90d),
            avg_days_late=float(customer.avg_days_late),
            broken_promise_rate=broken_rate,
            dispute_rate=dispute_rate,
        )
        for _ in range(touches_per_customer):
            arm_idx = int(rng.integers(0, len(ARMS)))
            arm = ARMS[arm_idx]
            latent = true_response_rate(customer.archetype, arm)
            latent += float(rng.normal(0.0, RESPONSE_NOISE_STD))
            latent = min(0.95, max(0.05, latent))
            reward = int(rng.random() < latent)
            day, part = arm.split("_", 1)
            records.append(
                {
                    "customer_id": customer.customer_id,
                    "segment": segment,
                    # Kept for analysis only: the bandit must never see it.
                    # The frame the bandit trains on drops this column.
                    "archetype": customer.archetype.value,
                    "arm": arm,
                    "weekday": day,
                    "daypart": part,
                    "reward": reward,
                    "logging_propensity": 1.0 / len(ARMS),
                }
            )

    frame = pd.DataFrame.from_records(records)
    return EngagementLog(
        frame=frame,
        customers=len(batch.customers),
        touches=len(frame),
        seed=seed,
        touches_per_customer=touches_per_customer,
    )


def training_frame(log: EngagementLog) -> pd.DataFrame:
    """The leakage-free view: everything except the hidden archetype."""

    return log.frame[ENGAGEMENT_COLUMNS].copy()


def write_dataset(log: EngagementLog, out_dir: str | Path) -> Path:
    """Persist the log, a customer feature table, and a build manifest."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    log.frame.to_csv(out / "engagement_log.csv", index=False)
    manifest = {
        "customers": log.customers,
        "touches": log.touches,
        "seed": log.seed,
        "touches_per_customer": log.touches_per_customer,
        "arms": list(ARMS),
        "logging_policy": "uniform",
        "logging_propensity": 1.0 / len(ARMS),
        "reward_definition": "customer responded (opened/replied) within 48h of the touch",
        "provenance": (
            "Response labels from the archetype response model in "
            "src/ml/contact_timing/dataset.py (RESPONSE_MODEL): a behavioural "
            "assumption, not measured inbox data. Segment features are identical "
            "to production Customer rows."
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out
