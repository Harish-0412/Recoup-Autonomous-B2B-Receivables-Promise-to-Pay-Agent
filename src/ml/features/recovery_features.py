"""Recovery-model features: computed in exactly one place.

The single most common way a model that works offline fails in production is
train/serve skew -- the training script and the serving path compute a feature
slightly differently, and nobody notices because both are individually correct.
The defence here is structural rather than procedural: there is one pure
function, both paths import it, and ``tests/ml/test_recovery_features.py``
asserts byte-identical output from a simulated training path and a simulated
inference path.

Two rules this module holds to:

* **``as_of`` is explicit.** Without it the function would implicitly mean
  "now", which is right at inference and wrong the moment you iterate historical
  rows to build a training set. Every time-dependent feature is computed
  relative to the passed instant.
* **No label ever enters.** ``recovered`` and ``recovered_date`` are not
  reachable from a ``CaseSnapshot`` -- the adapter in ``app.core.domain``
  refuses to carry them -- and a test asserts they never appear as keys here.
"""

import math
from datetime import date

from app.core.domain import CaseSnapshot

#: Version the feature set, never mutate it. A model artifact records which
#: version it was trained on, so a v2 that reorders or redefines a column
#: cannot be silently fed to a v1 model.
FEATURE_SET_VERSION = "recovery-features-v1"

#: The exact columns, in the exact order the model expects them.
FEATURE_COLUMNS_V1: tuple[str, ...] = (
    # customer history
    "customer_on_time_ratio_90d",
    "customer_on_time_ratio_all_time",
    "customer_avg_days_late",
    "customer_invoice_count",
    "customer_tenure_months",
    "customer_broken_promises_count",
    "customer_dispute_count",
    "customer_broken_promise_rate",
    "customer_dispute_rate",
    # invoice attributes
    "invoice_amount",
    "invoice_amount_log",
    "invoice_amount_vs_customer_avg_ratio",
    "days_overdue_at_scoring",
    "invoice_age_days",
    "payment_terms_days",
    "issued_day_of_week",
    "issued_day_of_month",
    # behavioural / interaction
    "days_since_last_contact",
    "prior_reminders_sent",
    "has_prior_promise",
    "prior_promise_kept",
    "current_escalation_tier",
    # derived
    "recency_weighted_on_time_score",
)

#: Neutral value for "there was no prior promise, so it was neither kept nor
#: broken". Zero would read as "broken", which is a different claim.
NO_PRIOR_PROMISE = 0.5

#: Sentinel the domain layer uses for "never contacted". Capped rather than
#: passed through, so one 9,999 does not dominate a tree split or blow up a
#: linear model's scale.
MAX_DAYS_SINCE_CONTACT = 180.0


def _clamp(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def recency_weighted_on_time_score(
    on_time_ratio_90d: float,
    on_time_ratio_all_time: float,
    invoice_count: int,
    *,
    half_life_invoices: float = 12.0,
) -> float:
    """Blend recent and long-run punctuality, weighting recent more heavily.

    The weight on the 90-day figure decays with how much history exists: for a
    customer with three invoices the recent ratio is mostly noise and the
    long-run figure should dominate, while for one with eighty the recent
    window is the more informative signal. This is the feature that lets the
    model see a RISK_ESCALATING customer -- historically good, lately not --
    as different from a steadily reliable one.
    """

    weight = 1.0 - math.exp(-max(invoice_count, 0) / max(half_life_invoices, 1e-6))
    recent_weight = 0.35 + 0.45 * weight
    return float(recent_weight * on_time_ratio_90d + (1.0 - recent_weight) * on_time_ratio_all_time)


def build_recovery_features(case: CaseSnapshot, as_of: date | None = None) -> dict[str, float]:
    """Compute the recovery feature vector for one invoice, as of an instant.

    Pure: no database, no clock, no randomness, no reference to the outcome.
    The same inputs always give the same output, which is what makes the
    train/serve parity test meaningful.
    """

    invoice = case.invoice
    customer = case.customer
    reference = as_of or invoice.as_of

    average_amount = max(customer.avg_invoice_amount, 1.0)
    size_ratio = invoice.amount / average_amount

    days_overdue = max((reference - invoice.due_date).days, 0)
    invoice_age = max((reference - invoice.issue_date).days, 0)

    prior_promise_kept = (
        NO_PRIOR_PROMISE
        if not invoice.has_prior_promise or invoice.prior_promise_kept is None
        else float(invoice.prior_promise_kept)
    )

    features: dict[str, float] = {
        "customer_on_time_ratio_90d": float(customer.on_time_ratio_90d),
        "customer_on_time_ratio_all_time": float(customer.on_time_ratio_all_time),
        "customer_avg_days_late": _clamp(float(customer.avg_days_late), 0.0, 120.0),
        "customer_invoice_count": float(customer.invoice_count),
        "customer_tenure_months": float(customer.tenure_months),
        "customer_broken_promises_count": float(customer.prior_broken_promises_count),
        "customer_dispute_count": float(customer.prior_disputes_count),
        "customer_broken_promise_rate": float(customer.broken_promise_rate),
        "customer_dispute_rate": float(customer.dispute_rate),
        "invoice_amount": float(invoice.amount),
        # Amounts are lognormal across three orders of magnitude; the log makes
        # the linear baseline usable without changing what the trees can see.
        "invoice_amount_log": float(math.log1p(max(invoice.amount, 0.0))),
        "invoice_amount_vs_customer_avg_ratio": _clamp(size_ratio, 0.0, 50.0),
        "days_overdue_at_scoring": float(days_overdue),
        "invoice_age_days": float(invoice_age),
        "payment_terms_days": float(invoice.payment_terms_days),
        "issued_day_of_week": float(invoice.issue_date.weekday()),
        "issued_day_of_month": float(invoice.issue_date.day),
        "days_since_last_contact": _clamp(
            float(invoice.days_since_last_contact), 0.0, MAX_DAYS_SINCE_CONTACT
        ),
        "prior_reminders_sent": float(invoice.prior_reminders_sent),
        "has_prior_promise": float(invoice.has_prior_promise),
        "prior_promise_kept": prior_promise_kept,
        "current_escalation_tier": float(invoice.ladder_index),
        "recency_weighted_on_time_score": recency_weighted_on_time_score(
            customer.on_time_ratio_90d,
            customer.on_time_ratio_all_time,
            customer.invoice_count,
        ),
    }

    # Declared and produced must not drift apart; catching it here beats
    # catching it as a silent column mismatch at predict time.
    if set(features) != set(FEATURE_COLUMNS_V1):
        missing = sorted(set(FEATURE_COLUMNS_V1) - set(features))
        extra = sorted(set(features) - set(FEATURE_COLUMNS_V1))
        raise RuntimeError(
            f"feature set drift: missing={missing} extra={extra}. "
            "Changing the feature set means adding a V2, not editing V1."
        )

    return features


def feature_vector(case: CaseSnapshot, as_of: date | None = None) -> list[float]:
    """The same features as an ordered vector, for a model's ``predict``."""

    features = build_recovery_features(case, as_of)
    return [features[column] for column in FEATURE_COLUMNS_V1]
