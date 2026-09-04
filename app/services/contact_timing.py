"""Serve the contact-timing bandit to the scheduler, and learn from replies.

Two directions, kept in one module so the loop is visible in one place:

* **Serve.** :func:`suggest_for_case` maps a decision snapshot to a segment,
  Thompson-samples the bandit, and returns the next calendar occurrence of the
  winning slot. No database needed: everything comes from the snapshot the
  agent already decided on.
* **Learn.** :func:`record_reply_engagement` folds one genuine reply into the
  posterior for (customer segment, last-contact arm) and re-persists the
  artifact. Guarded and silent on failure -- a learning update must never
  break the reply path that produced it.

When no trained artifact exists, suggestions degrade to a deterministic slot
(next weekday 09:30 IST) labelled ``model_unavailable``. A scheduler that
crashed for want of a model file would be worse than one that sends at a
sensible default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from src.ml.contact_timing.bandit import TimingBandit
from src.ml.contact_timing.segments import (
    GLOBAL_SEGMENT,
    arm_daypart_hour,
    segment_id,
)

logger = get_logger(__name__)

#: IST. Slots are defined in Indian business hours and the deployment target
#: is IST, so scheduling is computed against a fixed +05:30 offset rather than
#: whatever the server's local timezone happens to be.
IST = timezone(timedelta(hours=5, minutes=30))

#: Deterministic fallback slot when no model exists: next weekday 09:30 IST.
FALLBACK_WEEKDAY_HOUR = (0, 9, 30)


class TimingSuggestion(BaseModel):
    """One send-time recommendation, served or fallback."""

    model_config = ConfigDict(protected_namespaces=())

    customer_id: str
    segment: str
    arm: str
    scheduled_for: datetime
    expected_response_rate: float = Field(ge=0.0, le=1.0)
    observations: int = Field(ge=0)
    backed_off_to_global: bool = False
    fallback_used: bool = False
    fallback_reason: str = ""


_CACHED: tuple[TimingBandit, Any] | None = None


def clear_timing_cache() -> None:
    """Forget the loaded bandit. Tests use this to isolate artifact dirs."""

    global _CACHED
    _CACHED = None


def load_bandit() -> tuple[TimingBandit, Any] | None:
    """The trained bandit and its metadata, or None when no artifact exists."""

    global _CACHED
    if _CACHED is not None:
        return _CACHED
    try:
        from src.ml.contact_timing.artifacts import load_timing_bandit

        _CACHED = load_timing_bandit()
    except Exception as exc:  # no artifact yet, corrupt file -- serve fallback
        logger.debug("Contact-timing artifact unavailable", error=str(exc))
        _CACHED = None
    return _CACHED


def segment_for_snapshot(customer: Any) -> str:
    """Segment id from a customer snapshot or ORM row. Same features either way."""

    invoice_count = max(int(customer.invoice_count or 0), 1)
    return segment_id(
        on_time_ratio_90d=float(customer.on_time_ratio_90d or 0.0),
        avg_days_late=float(customer.avg_days_late or 0.0),
        broken_promise_rate=min(
            int(customer.prior_broken_promises_count or 0) / invoice_count, 1.0
        ),
        dispute_rate=min(int(customer.prior_disputes_count or 0) / invoice_count, 1.0),
    )


def next_occurrence(arm: str, *, now: datetime | None = None) -> datetime:
    """Next future datetime matching a send-time arm, in IST."""

    from src.ml.versioning import utc_now

    reference = (now or utc_now()).astimezone(IST)
    weekday, hour, minute = arm_daypart_hour(arm)
    days_ahead = (weekday - reference.weekday()) % 7
    candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )
    if candidate <= reference:
        candidate += timedelta(days=7)
    # Skip weekends defensively: arms never name them, but a fallback weekday
    # arithmetic slip must not schedule a Sunday send.
    while candidate.weekday() > 4:
        candidate += timedelta(days=1)
    return candidate


def fallback_suggestion(customer_id: str, *, segment: str = GLOBAL_SEGMENT) -> TimingSuggestion:
    """Deterministic slot when the model is unavailable. Labelled, never silent."""

    from src.ml.versioning import utc_now

    reference = utc_now().astimezone(IST)
    weekday, hour, minute = FALLBACK_WEEKDAY_HOUR
    days_ahead = (weekday - reference.weekday()) % 7
    candidate = reference.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )
    if candidate <= reference:
        candidate += timedelta(days=7)
    while candidate.weekday() > 4:
        candidate += timedelta(days=1)
    return TimingSuggestion(
        customer_id=customer_id,
        segment=segment,
        arm="fallback_weekday_morning",
        scheduled_for=candidate,
        expected_response_rate=0.0,
        observations=0,
        fallback_used=True,
        fallback_reason="model_unavailable",
    )


def suggest_for_case(case: Any, *, now: datetime | None = None) -> TimingSuggestion:
    """Recommend a send time for one decided case. Never raises for no model."""

    customer = case.customer
    segment = segment_for_snapshot(customer)
    loaded = load_bandit()
    if loaded is None:
        return fallback_suggestion(customer.customer_id, segment=segment)
    bandit, _ = loaded
    suggestion = bandit.suggest(segment)
    return TimingSuggestion(
        customer_id=customer.customer_id,
        segment=segment,
        arm=suggestion.arm,
        scheduled_for=next_occurrence(suggestion.arm, now=now),
        expected_response_rate=round(suggestion.expected_response_rate, 6),
        observations=suggestion.observations,
        backed_off_to_global=suggestion.backed_off_to_global,
    )


def record_reply_engagement(
    *,
    segment: str,
    arm: str | None,
    settings: Any | None = None,
) -> bool:
    """Fold one genuine reply into the posterior. Returns whether it learned.

    Reward is always 1: the call site only invokes this when a reply arrived.
    Non-replies are the implicit zeros already in the log. Opt-outs must never
    reach here -- teaching the bandit that a slot "works" because it provoked
    a STOP would optimise for harassment; the caller enforces that.
    """

    if not arm:
        return False
    loaded = load_bandit()
    if loaded is None:
        return False
    bandit, metadata = loaded
    try:
        bandit.update(segment, arm, reward=1)
    except ValueError:
        return False

    try:
        from src.ml.contact_timing.artifacts import save_timing_bandit

        metrics = dict(metadata.metrics or {})
        metrics["online_updates"] = float(metrics.get("online_updates", 0.0)) + 1.0
        save_timing_bandit(
            bandit,
            metrics=metrics,
            notes=(metadata.notes or "") + " +online",
            version=metadata.model_version,
            settings=settings,
        )
        clear_timing_cache()
    except Exception as exc:  # learning must never break serving
        logger.warning("Contact-timing online update failed", error=str(exc))
        return False
    return True
