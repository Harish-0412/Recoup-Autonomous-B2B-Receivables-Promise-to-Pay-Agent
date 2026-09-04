"""Unit tests for the engagement-log dataset builder."""

import pandas as pd

from src.ml.contact_timing.dataset import (
    build_engagement_log,
    training_frame,
    true_response_rate,
)
from src.data.synthetic_generator import CustomerArchetype
from src.ml.contact_timing.segments import ARMS


def test_log_schema_and_uniform_logging():
    log = build_engagement_log(customer_count=60, touches_per_customer=4, seed=7)

    assert log.customers == 60
    assert log.touches == 240
    assert set(log.frame["arm"].unique()) <= set(ARMS)
    assert set(log.frame["reward"].unique()) <= {0, 1}
    # Uniform logging policy: every arm pulled ~equally (spread within one mean).
    counts = log.frame["arm"].value_counts()
    assert counts.max() - counts.min() <= counts.mean()
    assert (log.frame["logging_propensity"] == 1.0 / len(ARMS)).all()


def test_seed_reproduces_byte_identical_log():
    first = build_engagement_log(customer_count=40, touches_per_customer=3, seed=11)
    second = build_engagement_log(customer_count=40, touches_per_customer=3, seed=11)
    pd.testing.assert_frame_equal(first.frame, second.frame)


def test_training_frame_cannot_reach_the_archetype():
    log = build_engagement_log(customer_count=40, touches_per_customer=3, seed=11)
    frame = training_frame(log)
    assert "archetype" not in frame.columns
    assert list(frame.columns) == [
        "customer_id",
        "segment",
        "arm",
        "weekday",
        "daypart",
        "reward",
        "logging_propensity",
    ]


def test_response_model_prefers_documented_slots():
    assert (
        true_response_rate(CustomerArchetype.RELIABLE, "mon_morning")
        > true_response_rate(CustomerArchetype.RELIABLE, "fri_late")
    )
    assert (
        true_response_rate(CustomerArchetype.ERRATIC, "thu_late")
        > true_response_rate(CustomerArchetype.ERRATIC, "mon_morning")
    )
    for arm in ARMS:
        rate = true_response_rate(CustomerArchetype.NEW_UNKNOWN, arm)
        assert 0.05 <= rate <= 0.95
