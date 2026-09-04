"""Send-time arms and customer segments.

Arms are weekday x daypart slots in IST business hours. Weekends are excluded:
this is B2B collections, and a Saturday 9pm reminder is a goodwill incident,
not an experiment.

Segments are built from observable payment behaviour only. The generator's
hidden archetype must never reach the bandit -- it is the ground truth the
evaluation measures against, and leaking it would be scoring the model on the
answer sheet.
"""

from __future__ import annotations

#: (daypart, hour, minute) in IST. Half hours sit inside business hours while
#: avoiding the exact top-of-hour inbox avalanche from other senders.
DAYPARTS: tuple[tuple[str, int, int], ...] = (
    ("morning", 9, 30),
    ("midday", 12, 30),
    ("late", 16, 30),
)

#: Monday=0 .. Friday=4. date.weekday() maps directly.
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")

#: Every arm the bandit may pull, e.g. "tue_midday".
ARMS: tuple[str, ...] = tuple(f"{day}_{part}" for day in WEEKDAYS for part, _, _ in DAYPARTS)

#: Segment used when a customer has no history yet (or no row at all).
GLOBAL_SEGMENT = "*"

#: Weakly informative prior: Beta(2, 2) centres new arms at 0.5 with the
#: weight of four pseudo-observations, so the first real touches move the
#: posterior quickly instead of drowning in a strong wrong prior.
PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0


def segment_id(
    *,
    on_time_ratio_90d: float,
    avg_days_late: float,
    broken_promise_rate: float,
    dispute_rate: float,
) -> str:
    """Map observable customer features to a segment id.

    Three on-time buckets x three lateness buckets x flagged/clean. The flag
    is a rate comparison, not a count, so long-standing customers are not
    punished for being long-standing -- the same normalisation the scorer
    uses for the same reason.
    """

    if on_time_ratio_90d >= 0.8:
        ontime = "reliable"
    elif on_time_ratio_90d >= 0.4:
        ontime = "mixed"
    else:
        ontime = "strained"

    if avg_days_late <= 3.0:
        late = "prompt"
    elif avg_days_late <= 15.0:
        late = "slow"
    else:
        late = "verylate"

    flag = "flagged" if (broken_promise_rate > 0.0 or dispute_rate > 0.0) else "clean"
    return f"{ontime}_{late}_{flag}"


def arm_daypart_hour(arm: str) -> tuple[int, int, int]:
    """Split an arm id into (weekday, hour, minute). Raises on unknown arms."""

    try:
        day, part = arm.split("_", 1)
        weekday = WEEKDAYS.index(day)
        _, hour, minute = next(d for d in DAYPARTS if d[0] == part)
    except (ValueError, StopIteration) as exc:
        raise ValueError(f"unknown send-time arm: {arm!r}") from exc
    return weekday, hour, minute
