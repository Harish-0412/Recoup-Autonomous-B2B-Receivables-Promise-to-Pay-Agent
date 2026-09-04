"""Unit tests for arms, segments, and slot arithmetic."""

import pytest

from src.ml.contact_timing.segments import (
    ARMS,
    DAYPARTS,
    WEEKDAYS,
    arm_daypart_hour,
    segment_id,
)


def test_fifteen_weekday_arms_no_weekends():
    assert len(ARMS) == 15
    assert len(set(ARMS)) == 15
    assert not any(arm.startswith(("sat", "sun")) for arm in ARMS)
    assert len(WEEKDAYS) == 5 and len(DAYPARTS) == 3


def test_segment_buckets_cover_the_ranges():
    assert segment_id(
        on_time_ratio_90d=0.95, avg_days_late=1.0, broken_promise_rate=0.0, dispute_rate=0.0
    ) == "reliable_prompt_clean"
    assert segment_id(
        on_time_ratio_90d=0.6, avg_days_late=10.0, broken_promise_rate=0.0, dispute_rate=0.0
    ) == "mixed_slow_clean"
    assert segment_id(
        on_time_ratio_90d=0.2, avg_days_late=30.0, broken_promise_rate=0.5, dispute_rate=0.0
    ) == "strained_verylate_flagged"
    # Boundary values belong somewhere deterministic.
    assert segment_id(
        on_time_ratio_90d=0.8, avg_days_late=3.0, broken_promise_rate=0.0, dispute_rate=0.0
    ) == "reliable_prompt_clean"


def test_dispute_rate_alone_flags():
    assert segment_id(
        on_time_ratio_90d=0.9, avg_days_late=1.0, broken_promise_rate=0.0, dispute_rate=0.1
    ).endswith("flagged")


def test_arm_parsing_round_trips():
    assert arm_daypart_hour("tue_midday") == (1, 12, 30)
    assert arm_daypart_hour("fri_late") == (4, 16, 30)
    assert arm_daypart_hour("mon_morning") == (0, 9, 30)
    with pytest.raises(ValueError):
        arm_daypart_hour("sun_midnight")
