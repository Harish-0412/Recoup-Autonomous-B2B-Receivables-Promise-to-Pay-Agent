"""Expected-value prioritization: which invoices are worth acting on.

    EV = P(recovery) x outstanding x urgency_weight - intervention_cost

The framing is the mirror image of standard credit-risk practice. A lender
computes Expected Loss as PD x EAD x LGD; here the same decomposition runs the
other way -- probability of recovery, exposure still outstanding, and a time
weight -- to produce an Expected *Recovery*. The reference projects cited in
``docs/architecture.md`` walk through the credit-scoring side of that parallel.

**This is the rules-based scorer, and it says so.** Phase 4 replaces
``estimate_recovery_probability`` with a trained, calibrated model. Until then
every prediction is returned with ``fallback_used=True`` and
``resolved_by=RULES_BASED_SCORER``, so nothing downstream can mistake a
hand-tuned logistic for a fitted one, and the batch report says which produced
its numbers. The coefficients below are deliberately *not* copied from the
synthetic generator's outcome model -- a scorer fitted by hand to the exact
data-generating process would report an accuracy that means nothing.

The false-intervention cost is why ``WAIT`` exists at all. Contacting a
customer who was about to pay anyway is not free, and the batch report counts
those as a cost rather than quietly dropping them.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import CaseSnapshot
from app.models.enums import InterventionTier
from src.ml.schemas import (
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    FeatureDriver,
    RecoveryScorePrediction,
)
from src.ml.versioning import utc_now

SCORER_VERSION = "rules-scorer-v1"

#: Rupee cost of one automated intervention: sending, the reply handling it
#: generates, and the goodwill it spends. Small, but not zero -- if it were
#: zero the optimal policy would be to contact everyone constantly.
DEFAULT_INTERVENTION_COST = 250.0

#: Above this, act now; between the two, remind; below, wait.
DEFAULT_ESCALATE_THRESHOLD = 25_000.0
DEFAULT_REMIND_THRESHOLD = 2_500.0


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class ScoringConfig(BaseModel):
    """Thresholds and costs governing the prioritization decision."""

    model_config = ConfigDict(protected_namespaces=())

    intervention_cost: float = Field(default=DEFAULT_INTERVENTION_COST, ge=0.0)
    escalate_threshold: float = Field(default=DEFAULT_ESCALATE_THRESHOLD)
    remind_threshold: float = Field(default=DEFAULT_REMIND_THRESHOLD)
    #: Recovery horizon the probability refers to, in days.
    horizon_days: int = Field(default=30, gt=0)
    #: Above this recovery probability an invoice is left alone regardless of
    #: value: the customer is about to pay, and contacting them is a false
    #: intervention rather than a win.
    #:
    #: 0.75 is a business setting, not a fitted one, and it is the lever that
    #: trades false interventions against missed recoveries. It is set here so
    #: that the README's INV-1044 (97% on-time, two days overdue) is left alone,
    #: which is the behaviour that walkthrough describes. Raise it to chase more
    #: aggressively; the batch report counts the false interventions either way.
    self_cure_probability: float = Field(default=0.75, ge=0.0, le=1.0)


class InvoiceScore(BaseModel):
    """The scorer's full verdict on one invoice."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    customer_id: str
    outstanding: float
    p_recovery: float = Field(ge=0.0, le=1.0)
    urgency_weight: float = Field(ge=0.0)
    expected_recovery: float
    expected_value: float
    tier: InterventionTier
    rationale: str
    prediction: RecoveryScorePrediction

    @property
    def is_actionable(self) -> bool:
        return self.tier is not InterventionTier.WAIT


def urgency_weight(days_overdue: int, *, horizon_days: int = 30) -> float:
    """How much the *timing* of this invoice raises its claim on attention.

    Rises quickly through the first weeks overdue, then flattens: the
    difference between 5 and 25 days overdue is large, the difference between
    95 and 115 is not -- by then the problem is collectability, not ageing.
    """

    if days_overdue <= 0:
        return 0.35
    return 0.35 + 0.65 * (1.0 - math.exp(-days_overdue / max(horizon_days / 2.0, 1.0)))


def estimate_recovery_probability(case: CaseSnapshot) -> tuple[float, list[FeatureDriver]]:
    """Rules-based P(paid within the horizon), with per-feature contributions.

    A logistic model with hand-set coefficients. Returning the contributions
    alongside the probability keeps the explainability contract identical to
    the one the trained model will satisfy through SHAP -- callers read
    ``top_drivers`` either way and do not branch on which scorer ran.
    """

    customer = case.customer
    invoice = case.invoice

    size_ratio = invoice.amount / max(customer.avg_invoice_amount, 1.0)

    contributions: list[tuple[str, float, float]] = [
        # (feature, observed value, contribution to the log-odds)
        (
            "on_time_ratio_90d",
            customer.on_time_ratio_90d,
            1.60 * (customer.on_time_ratio_90d - 0.6),
        ),
        (
            "on_time_ratio_all_time",
            customer.on_time_ratio_all_time,
            0.70 * (customer.on_time_ratio_all_time - 0.6),
        ),
        ("avg_days_late", customer.avg_days_late, -0.022 * min(customer.avg_days_late, 60.0)),
        ("invoice_size_ratio", size_ratio, -0.40 * math.log(max(size_ratio, 1e-3))),
        ("days_overdue", float(invoice.days_overdue), -0.014 * invoice.days_overdue),
        (
            "broken_promise_rate",
            customer.broken_promise_rate,
            -1.40 * customer.broken_promise_rate,
        ),
        ("dispute_rate", customer.dispute_rate, -0.95 * customer.dispute_rate),
        ("escalation_tier", float(invoice.ladder_index), -0.12 * invoice.ladder_index),
        (
            "tenure_months",
            float(customer.tenure_months),
            0.010 * min(customer.tenure_months, 36.0),
        ),
    ]

    if invoice.has_prior_promise:
        kept = bool(invoice.prior_promise_kept)
        contributions.append(("prior_promise_kept", float(kept), 0.55 if kept else -0.80))

    # Intercept: a mid-book overdue invoice is more likely than not to be
    # recovered inside the horizon. Everything above moves it from there.
    intercept = 0.45
    logit = intercept + sum(contribution for _, _, contribution in contributions)

    drivers = sorted(
        (
            FeatureDriver(feature=name, value=round(value, 4), shap_contribution=round(delta, 4))
            for name, value, delta in contributions
        ),
        key=lambda driver: abs(driver.shap_contribution),
        reverse=True,
    )
    return _sigmoid(logit), drivers


def score_case(case: CaseSnapshot, config: ScoringConfig | None = None) -> InvoiceScore:
    """Score one invoice and recommend an intervention tier."""

    settings = config or ScoringConfig()
    invoice = case.invoice

    probability, drivers = estimate_recovery_probability(case)
    weight = urgency_weight(invoice.days_overdue, horizon_days=settings.horizon_days)
    outstanding = invoice.outstanding

    # Expected recovery if we act. The value *at risk* -- and therefore worth
    # spending an intervention on -- is what a non-certain recovery leaves on
    # the table, which is why (1 - p) appears rather than p.
    expected_recovery = probability * outstanding
    value_at_risk = (1.0 - probability) * outstanding * weight
    expected_value = value_at_risk - settings.intervention_cost

    prediction = RecoveryScorePrediction(
        invoice_id=invoice.invoice_id,
        p_recovery_30d=round(probability, 6),
        calibrated=False,
        top_drivers=drivers[:5],
        model_version=SCORER_VERSION,
        confidence=round(abs(probability - 0.5) * 2.0, 6),
        fallback_used=True,
        fallback=FallbackResult(
            triggered=True,
            reason=FallbackReason.MODEL_LOAD_FAILED,
            resolved_by=FallbackResolver.RULES_BASED_SCORER,
        ),
        scored_at=utc_now(),
    )

    tier, rationale = _decide_tier(
        probability=probability,
        expected_value=expected_value,
        settings=settings,
        case=case,
    )

    return InvoiceScore(
        invoice_id=invoice.invoice_id,
        customer_id=invoice.customer_id,
        outstanding=round(outstanding, 2),
        p_recovery=round(probability, 6),
        urgency_weight=round(weight, 4),
        expected_recovery=round(expected_recovery, 2),
        expected_value=round(expected_value, 2),
        tier=tier,
        rationale=rationale,
        prediction=prediction,
    )


def _decide_tier(
    *,
    probability: float,
    expected_value: float,
    settings: ScoringConfig,
    case: CaseSnapshot,
) -> tuple[InterventionTier, str]:
    """Turn an expected value into a recommendation, with its reason."""

    invoice = case.invoice

    if not invoice.is_actionable:
        return (
            InterventionTier.WAIT,
            f"Invoice status is {invoice.status.value}; no agent action applies.",
        )

    if probability >= settings.self_cure_probability:
        return (
            InterventionTier.WAIT,
            (
                f"P(recovery)={probability:.2f} is above the {settings.self_cure_probability:.2f} "
                "self-cure threshold; contacting now would most likely be a false intervention."
            ),
        )

    if expected_value >= settings.escalate_threshold:
        return (
            InterventionTier.ESCALATE,
            (
                f"Value at risk nets EV=Rs {expected_value:,.0f} after intervention cost, "
                f"above the Rs {settings.escalate_threshold:,.0f} escalation threshold."
            ),
        )

    if expected_value >= settings.remind_threshold:
        return (
            InterventionTier.REMIND,
            (
                f"EV=Rs {expected_value:,.0f} clears the Rs {settings.remind_threshold:,.0f} "
                "reminder threshold but not the escalation one; a single reminder is proportionate."
            ),
        )

    return (
        InterventionTier.WAIT,
        (
            f"EV=Rs {expected_value:,.0f} does not clear the Rs "
            f"{settings.remind_threshold:,.0f} reminder threshold; acting would cost more "
            "than the value it protects."
        ),
    )


def rank_cases(
    cases: list[CaseSnapshot], config: ScoringConfig | None = None
) -> list[InvoiceScore]:
    """Score every case and return them highest expected value first."""

    settings = config or ScoringConfig()
    scores = [score_case(case, settings) for case in cases]
    return sorted(scores, key=lambda score: score.expected_value, reverse=True)
