"""Tests for the contact-timing serving layer: suggest, schedule, learn."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import contact_timing
from src.ml.config import MLSettings
from src.ml.contact_timing.bandit import TimingBandit
from src.ml.schemas import ModelMetadata
from src.ml.versioning import utc_now


def _case(customer_id="C-9", **overrides):
    fields = {
        "customer_id": customer_id,
        "invoice_count": 20,
        "on_time_ratio_90d": 0.9,
        "avg_days_late": 2.0,
        "prior_broken_promises_count": 0,
        "prior_disputes_count": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(customer=SimpleNamespace(**fields))


def test_suggest_for_case_without_artifact_falls_back_labelled(monkeypatch):
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: None)
    suggestion = contact_timing.suggest_for_case(_case())
    assert suggestion.fallback_used is True
    assert suggestion.fallback_reason == "model_unavailable"
    assert suggestion.segment == "reliable_prompt_clean"
    assert suggestion.scheduled_for.weekday() < 5


def test_suggest_for_case_serves_bandit(monkeypatch):
    bandit = TimingBandit()
    bandit.fit_rows([("reliable_prompt_clean", "wed_midday", 1)] * 10)
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: (bandit, None))
    suggestion = contact_timing.suggest_for_case(_case())
    assert suggestion.fallback_used is False
    assert suggestion.segment == "reliable_prompt_clean"
    assert suggestion.expected_response_rate >= 0.5


def test_next_occurrence_lands_on_the_right_slot():
    kolkata = ZoneInfo("Asia/Kolkata")
    # Monday 08:00 IST: mon_morning (09:30) is later today; tue_midday ~28h out.
    early = datetime(2026, 9, 7, 8, 0, tzinfo=kolkata)
    mon = contact_timing.next_occurrence("mon_morning", now=early)
    assert (mon.weekday(), mon.hour, mon.minute) == (0, 9, 30)
    assert (mon.date() - early.date()).days == 0
    tue = contact_timing.next_occurrence("tue_midday", now=early)
    assert (tue.weekday(), tue.hour, tue.minute) == (1, 12, 30)
    # Monday 10:00 IST: mon_morning already passed -> same slot next week.
    late = datetime(2026, 9, 7, 10, 0, tzinfo=kolkata)
    mon_next = contact_timing.next_occurrence("mon_morning", now=late)
    assert (mon_next.weekday(), mon_next.hour, mon_next.minute) == (0, 9, 30)
    assert (mon_next.date() - late.date()).days == 7


def test_record_reply_engagement_updates_and_persists(tmp_path, monkeypatch):
    bandit = TimingBandit()
    metadata = ModelMetadata(
        model_name="contact-timing-bandit",
        model_version="test-v1",
        trained_at=utc_now(),
        train_rows=0,
        feature_columns=["segment", "arm"],
        metrics={},
    )
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: (bandit, metadata))
    # NOTE: MLSettings ignores constructor kwargs for aliased fields; the env
    # var is the supported override path.
    monkeypatch.setenv("ML_ARTIFACTS_DIR", str(tmp_path))

    before = bandit.expected_rate("reliable_prompt_clean", "tue_midday")
    assert contact_timing.record_reply_engagement(
        segment="reliable_prompt_clean", arm="tue_midday"
    ) is True
    assert bandit.expected_rate("reliable_prompt_clean", "tue_midday") > before
    assert (tmp_path / "contact-timing-bandit" / "test-v1" / "model.joblib").exists()


def test_record_reply_engagement_rejects_unknown_arm(monkeypatch):
    bandit = TimingBandit()
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: (bandit, None))
    assert (
        contact_timing.record_reply_engagement(
            segment="reliable_prompt_clean", arm="sunday_midnight"
        )
        is False
    )
    assert contact_timing.record_reply_engagement(segment="s", arm=None) is False


def test_record_reply_engagement_without_model_is_quiet(monkeypatch):
    monkeypatch.setattr(contact_timing, "load_bandit", lambda: None)
    assert (
        contact_timing.record_reply_engagement(
            segment="reliable_prompt_clean", arm="tue_midday"
        )
        is False
    )


def test_segment_for_snapshot_normalises_counts():
    row = SimpleNamespace(
        invoice_count=100,
        on_time_ratio_90d=0.9,
        avg_days_late=1.0,
        prior_broken_promises_count=5,
        prior_disputes_count=0,
    )
    # 5 broken of 100 is a rate of 0.05 -- still flagged, but a *rate*.
    assert contact_timing.segment_for_snapshot(row) == "reliable_prompt_flagged"
    with pytest.raises(Exception):
        contact_timing.next_occurrence("sunday_midnight")
