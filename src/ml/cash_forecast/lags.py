"""Empirical payment-lag distributions, one per observable customer segment.

A "lag" is days from flag-date to paid-date, observed only for invoices that
were actually paid inside the observation horizon. That conditioning is
deliberate and must stay paired with its probability: the recovery scorer
answers "does it pay within 30 days", the lag table answers "given that it
pays within 30 days, on which day". Multiplying them is coherent; using
either one alone is not.

Segments come from ``src.ml.contact_timing.segments.segment_id`` -- observable
payment behaviour only (on-time ratio, lateness, broken-promise and dispute
rates). The hidden generator archetype is ground truth for evaluation and
must never enter a fit.

Rare segments back off to the pooled global distribution (the same fallback
discipline as the contact-timing bandit): a lag table fitted on six invoices
is a memorised anecdote, not a distribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.domain import CaseSnapshot
from src.ml.contact_timing.segments import GLOBAL_SEGMENT, segment_id


@dataclass(frozen=True)
class LagTables:
    """Fitted lag distributions. Plain data only, so joblib round-trips it."""

    #: Segment id -> sorted observed lags in days. Segments with fewer than
    #: ``min_segment_samples`` observations are absent here (see
    #: ``pooled_segments``) and resolve to ``global_lags`` at sample time.
    segments: dict[str, tuple[int, ...]] = field(default_factory=dict)
    #: Every observed lag, pooled. The fallback for rare/unknown segments.
    global_lags: tuple[int, ...] = ()
    #: Segments that existed in training but were pooled for sparsity.
    pooled_segments: tuple[str, ...] = ()
    min_segment_samples: int = 30
    horizon_days: int = 30
    train_rows: int = 0
    seed: int = 0

    def lags_for(self, segment: str) -> tuple[int, ...]:
        """Observed lags for a segment, or the pooled global table."""

        return self.segments.get(segment, self.global_lags)


def segment_for_case(case: CaseSnapshot) -> str:
    """Map a scored case to its lag segment, from observables only."""

    customer = case.customer
    return segment_id(
        on_time_ratio_90d=customer.on_time_ratio_90d,
        avg_days_late=customer.avg_days_late,
        broken_promise_rate=customer.broken_promise_rate,
        dispute_rate=customer.dispute_rate,
    )


def fit_lag_tables(
    rows: list[tuple[str, int]],
    *,
    min_segment_samples: int = 30,
    horizon_days: int = 30,
    seed: int = 0,
) -> LagTables:
    """Fit empirical lag tables from (segment, lag_days) observations.

    Lags at or below zero are clamped to 1 (paid the same day still lands
    one day out for bucketing); lags beyond ``horizon_days`` are dropped --
    they cannot have been observed inside the horizon, so keeping them would
    pretend the table knows about payments it has never seen.
    """

    if min_segment_samples < 1:
        raise ValueError("min_segment_samples must be positive")

    by_segment: dict[str, list[int]] = {}
    pooled: list[int] = []
    for segment, lag in rows:
        clamped = max(1, int(lag))
        if clamped > horizon_days:
            continue
        by_segment.setdefault(segment, []).append(clamped)
        pooled.append(clamped)

    if not pooled:
        raise ValueError("no observable lags to fit: rows is empty or all beyond the horizon")

    segments: dict[str, tuple[int, ...]] = {}
    pooled_names: list[str] = []
    for segment, lags in by_segment.items():
        if len(lags) >= min_segment_samples:
            segments[segment] = tuple(sorted(lags))
        else:
            pooled_names.append(segment)

    return LagTables(
        segments=segments,
        global_lags=tuple(sorted(pooled)),
        pooled_segments=tuple(sorted(pooled_names)),
        min_segment_samples=min_segment_samples,
        horizon_days=horizon_days,
        train_rows=len(pooled),
        seed=seed,
    )


def lags_for(tables: LagTables, segment: str) -> tuple[int, ...]:
    """Module-level alias for ``LagTables.lags_for`` (stable import surface)."""

    return tables.lags_for(segment) if segment != GLOBAL_SEGMENT else tables.global_lags
