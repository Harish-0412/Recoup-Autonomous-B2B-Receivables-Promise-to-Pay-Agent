"""Seeded synthetic data for Recoup.

This module is the **single** generator for the whole project. The rules-based
demo, the ML training pipeline and the reply-understanding tests all draw from
it, so there is exactly one definition of "what a Recoup customer looks like".

Design rules that matter downstream:

* **The archetype is hidden.** Each customer is drawn from a behavioural
  archetype that drives both their observable history and their payment
  outcome, but the archetype itself is never a model feature. It is declared
  with ``exclude=True`` so it cannot leak through ``model_dump()``, and
  :mod:`src.ml.data.export` asserts it is absent from every exported frame.
* **Outcomes are generated once, here.** ``simulate_outcomes`` samples the
  ground-truth ``recovered`` label, so every downstream model trains against
  the same source of truth.
* **Reproducibility is explicit.** Every draw goes through one seeded
  ``numpy.random.Generator`` threaded down by hand. There are no bare
  ``random`` calls, so a seed reproduces a batch exactly.
* **Outcomes are not perfectly predictable.** An irreducible noise term is
  added to the latent recovery logit on purpose. A simulator whose labels are a
  deterministic function of its features produces models with implausible
  accuracy and teaches you nothing.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from enum import Enum
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

GENERATOR_VERSION = "synthetic-generator-v1"

#: Fields that exist on the models below but must never reach a training frame.
HIDDEN_FIELDS: tuple[str, ...] = (
    "archetype",
    "true_recovery_probability",
    "eventual_payment_days",
)

INDUSTRIES: tuple[str, ...] = (
    "logistics",
    "textiles",
    "pharma distribution",
    "auto components",
    "IT services",
    "FMCG distribution",
    "construction materials",
    "packaging",
    "agri inputs",
    "electronics retail",
)

CHANNELS: tuple[str, ...] = ("email", "whatsapp")

PAYMENT_TERMS_DAYS: tuple[int, ...] = (15, 30, 45, 60)

_NAME_PREFIXES: tuple[str, ...] = (
    "Shree",
    "Bharat",
    "Ganesh",
    "Krishna",
    "Meenakshi",
    "Sunrise",
    "Apex",
    "Vertex",
    "Nova",
    "Deccan",
    "Konark",
    "Anand",
    "Vishwa",
    "Rajdhani",
    "Sagar",
    "Vaibhav",
    "Trident",
    "Hindustan",
    "Prime",
    "Sundaram",
)
_NAME_MIDDLES: tuple[str, ...] = (
    "Enterprises",
    "Traders",
    "Industries",
    "Agencies",
    "Solutions",
    "Logistics",
    "Distributors",
    "Exports",
    "Technologies",
    "Fabricators",
)
_NAME_SUFFIXES: tuple[str, ...] = ("Pvt Ltd", "LLP", "& Co", "Pvt Ltd", "Industries Ltd")


# ---------------------------------------------------------------------------
# Archetypes
# ---------------------------------------------------------------------------


class CustomerArchetype(str, Enum):
    """Hidden behavioural class driving a customer's history and outcomes."""

    RELIABLE = "RELIABLE"
    LATE_BUT_PAYS = "LATE_BUT_PAYS"
    ERRATIC = "ERRATIC"
    NEW_UNKNOWN = "NEW_UNKNOWN"
    RISK_ESCALATING = "RISK_ESCALATING"


class ArchetypeProfile(BaseModel):
    """Parameters that define how one archetype behaves.

    ``recovery_logit_base`` is the archetype's contribution to the latent
    log-odds of being paid; the observable-history and invoice terms are added
    on top of it in :func:`simulate_outcomes`.
    """

    model_config = ConfigDict(frozen=True)

    on_time_ratio_mean: float
    on_time_ratio_spread: float
    recent_drift: float
    avg_days_late_mean: float
    avg_days_late_spread: float
    promise_keep_rate: float
    dispute_propensity: float
    tenure_months_range: tuple[int, int]
    invoice_count_range: tuple[int, int]
    recovery_logit_base: float
    payment_delay_mean_days: float
    outcome_noise_scale: float


#: Tuned so the five archetypes are clearly separable in aggregate but overlap
#: at the individual level -- which is what makes the learning problem real.
ARCHETYPE_PROFILES: dict[CustomerArchetype, ArchetypeProfile] = {
    CustomerArchetype.RELIABLE: ArchetypeProfile(
        on_time_ratio_mean=0.93,
        on_time_ratio_spread=0.05,
        recent_drift=0.01,
        avg_days_late_mean=2.0,
        avg_days_late_spread=1.5,
        promise_keep_rate=0.95,
        dispute_propensity=0.02,
        tenure_months_range=(18, 96),
        invoice_count_range=(12, 80),
        recovery_logit_base=1.95,
        payment_delay_mean_days=5.0,
        outcome_noise_scale=0.45,
    ),
    CustomerArchetype.LATE_BUT_PAYS: ArchetypeProfile(
        on_time_ratio_mean=0.34,
        on_time_ratio_spread=0.09,
        recent_drift=0.00,
        avg_days_late_mean=16.0,
        avg_days_late_spread=5.0,
        promise_keep_rate=0.82,
        dispute_propensity=0.06,
        tenure_months_range=(12, 84),
        invoice_count_range=(10, 70),
        recovery_logit_base=3.38,
        payment_delay_mean_days=19.0,
        outcome_noise_scale=0.55,
    ),
    CustomerArchetype.ERRATIC: ArchetypeProfile(
        on_time_ratio_mean=0.47,
        on_time_ratio_spread=0.20,
        recent_drift=-0.03,
        avg_days_late_mean=24.0,
        avg_days_late_spread=16.0,
        promise_keep_rate=0.48,
        dispute_propensity=0.14,
        tenure_months_range=(6, 72),
        invoice_count_range=(6, 55),
        recovery_logit_base=1.56,
        payment_delay_mean_days=31.0,
        outcome_noise_scale=1.10,
    ),
    CustomerArchetype.NEW_UNKNOWN: ArchetypeProfile(
        on_time_ratio_mean=0.62,
        on_time_ratio_spread=0.22,
        recent_drift=0.00,
        avg_days_late_mean=9.0,
        avg_days_late_spread=8.0,
        promise_keep_rate=0.65,
        dispute_propensity=0.07,
        tenure_months_range=(0, 5),
        invoice_count_range=(0, 3),
        recovery_logit_base=1.86,
        payment_delay_mean_days=17.0,
        outcome_noise_scale=1.25,
    ),
    CustomerArchetype.RISK_ESCALATING: ArchetypeProfile(
        # Historically strong, recently deteriorating: the drift term pulls the
        # 90-day ratio well below the all-time ratio. This is the archetype that
        # makes drift monitoring (Phase 11) worth building.
        on_time_ratio_mean=0.84,
        on_time_ratio_spread=0.07,
        recent_drift=-0.46,
        avg_days_late_mean=13.0,
        avg_days_late_spread=6.0,
        promise_keep_rate=0.55,
        dispute_propensity=0.11,
        tenure_months_range=(14, 90),
        invoice_count_range=(14, 75),
        recovery_logit_base=1.39,
        payment_delay_mean_days=28.0,
        outcome_noise_scale=0.85,
    ),
}

#: Population mix. Deliberately imbalanced -- most real ledgers are.
ARCHETYPE_MIX: dict[CustomerArchetype, float] = {
    CustomerArchetype.RELIABLE: 0.34,
    CustomerArchetype.LATE_BUT_PAYS: 0.26,
    CustomerArchetype.ERRATIC: 0.18,
    CustomerArchetype.NEW_UNKNOWN: 0.12,
    CustomerArchetype.RISK_ESCALATING: 0.10,
}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class Customer(BaseModel):
    """A synthetic customer.

    Every field except ``archetype`` is observable to a model. ``archetype`` is
    marked ``exclude=True`` so it is dropped by ``model_dump()`` -- the leak has
    to be written on purpose, not forgotten into existence.
    """

    model_config = ConfigDict(protected_namespaces=())

    customer_id: str
    name: str
    industry: str
    preferred_channel: str
    tenure_months: int = Field(ge=0)
    invoice_count: int = Field(ge=0)
    avg_invoice_amount: float = Field(gt=0)
    on_time_ratio_90d: float = Field(ge=0.0, le=1.0)
    on_time_ratio_all_time: float = Field(ge=0.0, le=1.0)
    avg_days_late: float = Field(ge=0.0)
    prior_broken_promises_count: int = Field(ge=0)
    prior_disputes_count: int = Field(ge=0)

    archetype: CustomerArchetype = Field(exclude=True)


class Invoice(BaseModel):
    """A synthetic overdue invoice, as seen at the moment the agent flags it.

    ``flagged_date`` is the scoring "as of" date: every feature is computed as
    of that instant, and the label asks whether the invoice was recovered
    within ``horizon_days`` of it.
    """

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    customer_id: str
    amount: float = Field(gt=0)
    currency: str = "INR"
    issue_date: date
    due_date: date
    payment_terms_days: int = Field(gt=0)
    flagged_date: date
    days_overdue_at_flag: int = Field(ge=0)
    current_escalation_tier: int = Field(ge=0, le=3)
    prior_reminders_sent: int = Field(ge=0)
    days_since_last_contact: int = Field(ge=0)
    has_prior_promise: bool = False
    prior_promise_kept: bool | None = None

    # --- ground truth, filled by simulate_outcomes ---
    recovered: bool | None = None
    recovered_date: date | None = None

    # --- latent truth, never exported ---
    true_recovery_probability: float | None = Field(default=None, exclude=True)
    eventual_payment_days: int | None = Field(default=None, exclude=True)


class ReplySeedExample(BaseModel):
    """A label-first reply example.

    The intent and entities are known *before* the text exists, so the ground
    truth is correct by construction rather than by annotation.
    """

    model_config = ConfigDict(protected_namespaces=())

    reply_id: str
    invoice_id: str
    intent: str
    tone: str
    text: str
    reference_date: date
    expected_amount: float | None = None
    expected_date: date | None = None
    amount_checkable: bool = False
    date_checkable: bool = False
    unambiguous: bool = True


class SyntheticBatch(BaseModel):
    """One reproducible generation run."""

    model_config = ConfigDict(protected_namespaces=())

    seed: int
    generator_version: str
    horizon_days: int
    as_of: date
    timeline_days: int = 3
    customers: list[Customer]
    invoices: list[Invoice]
    reply_seed_examples: list[ReplySeedExample]

    def mature_invoices(self) -> list[Invoice]:
        """Invoices whose horizon has fully elapsed by ``as_of``.

        An invoice flagged ten days ago has no observed 30-day outcome yet.
        Training on one would teach the model that recent invoices are not
        recovered, which is an artefact of when the data was cut, not
        behaviour. Only mature rows carry a real label.
        """

        return [
            invoice
            for invoice in self.invoices
            if (self.as_of - invoice.flagged_date).days >= self.horizon_days
        ]

    def archetype_counts(self) -> dict[str, int]:
        """Aggregate archetype distribution -- manifest metadata, not a feature."""

        counts: dict[str, int] = {a.value: 0 for a in CustomerArchetype}
        for customer in self.customers:
            counts[customer.archetype.value] += 1
        return counts


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def make_rng(seed: int) -> np.random.Generator:
    """Return the one RNG the whole generator threads through."""

    return np.random.default_rng(seed)


def _sigmoid(x: float) -> float:
    return float(1.0 / (1.0 + np.exp(-x)))


def _clamp(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def _beta_around(rng: np.random.Generator, mean: float, spread: float) -> float:
    """Draw from a Beta distribution with the requested mean and rough spread."""

    mean = _clamp(mean, 0.02, 0.98)
    spread = max(spread, 1e-3)
    concentration = max((mean * (1.0 - mean)) / (spread**2) - 1.0, 1.0)
    alpha = mean * concentration
    beta = (1.0 - mean) * concentration
    return float(rng.beta(alpha, beta))


def _escalation_tier(days_overdue: int) -> int:
    if days_overdue < 7:
        return 0
    if days_overdue < 15:
        return 1
    if days_overdue < 30:
        return 2
    return 3


def _company_name(rng: np.random.Generator) -> str:
    prefix = _NAME_PREFIXES[int(rng.integers(len(_NAME_PREFIXES)))]
    middle = _NAME_MIDDLES[int(rng.integers(len(_NAME_MIDDLES)))]
    suffix = _NAME_SUFFIXES[int(rng.integers(len(_NAME_SUFFIXES)))]
    return f"{prefix} {middle} {suffix}"


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def generate_customers(n: int, rng: np.random.Generator) -> list[Customer]:
    """Generate ``n`` customers with archetype-consistent observable histories."""

    if n <= 0:
        raise ValueError("n must be positive")

    archetypes = list(ARCHETYPE_MIX)
    weights = np.array([ARCHETYPE_MIX[a] for a in archetypes], dtype=float)
    weights = weights / weights.sum()

    customers: list[Customer] = []
    for index in range(n):
        archetype = archetypes[int(rng.choice(len(archetypes), p=weights))]
        profile = ARCHETYPE_PROFILES[archetype]

        tenure_low, tenure_high = profile.tenure_months_range
        tenure_months = int(rng.integers(tenure_low, tenure_high + 1))

        count_low, count_high = profile.invoice_count_range
        invoice_count = int(rng.integers(count_low, count_high + 1))

        on_time_all_time = _beta_around(
            rng, profile.on_time_ratio_mean, profile.on_time_ratio_spread
        )

        # Recent behaviour = long-run behaviour + archetype drift + sampling
        # noise. Customers with almost no history get a much noisier recent
        # ratio, which is exactly why NEW_UNKNOWN is hard to score confidently.
        sample_noise = 0.06 + 0.55 / np.sqrt(max(invoice_count, 1))
        on_time_90d = _clamp(
            on_time_all_time + profile.recent_drift + float(rng.normal(0.0, sample_noise)),
            0.0,
            1.0,
        )

        avg_days_late = max(
            0.0,
            float(rng.normal(profile.avg_days_late_mean, profile.avg_days_late_spread)),
        )
        avg_invoice_amount = float(np.exp(rng.normal(11.4, 0.75)))
        avg_invoice_amount = _clamp(avg_invoice_amount, 12_000.0, 2_500_000.0)

        expected_promises = invoice_count * 0.30
        broken_promises = int(
            rng.poisson(max(expected_promises * (1.0 - profile.promise_keep_rate), 0.0))
        )
        disputes = int(rng.poisson(max(invoice_count * profile.dispute_propensity, 0.0)))

        customers.append(
            Customer(
                customer_id=f"CUS-{index + 1:04d}",
                name=_company_name(rng),
                industry=INDUSTRIES[int(rng.integers(len(INDUSTRIES)))],
                preferred_channel=CHANNELS[int(rng.integers(len(CHANNELS)))],
                tenure_months=tenure_months,
                invoice_count=invoice_count,
                avg_invoice_amount=round(avg_invoice_amount, 2),
                on_time_ratio_90d=round(on_time_90d, 4),
                on_time_ratio_all_time=round(on_time_all_time, 4),
                avg_days_late=round(avg_days_late, 2),
                prior_broken_promises_count=broken_promises,
                prior_disputes_count=disputes,
                archetype=archetype,
            )
        )

    return customers


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def generate_invoices(
    customers: list[Customer],
    rng: np.random.Generator,
    *,
    batch_size: int,
    as_of: date,
    timeline_days: int = 3,
) -> list[Invoice]:
    """Generate ``batch_size`` overdue invoices spread across ``customers``.

    ``timeline_days`` is how far back from ``as_of`` invoices may have been
    flagged. The default of 3 keeps a batch effectively "as of today", which is
    what the demo wants. Recovery-model training passes a long window instead,
    because a model that will predict forward has to be validated on a split by
    time rather than at random -- otherwise it is scored on a future it was
    partly trained on.
    """

    if not customers:
        raise ValueError("customers must not be empty")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    by_id = {customer.customer_id: customer for customer in customers}
    # Heavier customers carry more invoices, as in a real ledger -- but damped,
    # so low-history archetypes such as NEW_UNKNOWN are not starved out of the
    # batch entirely and stay learnable.
    activity = np.sqrt(np.array([c.invoice_count for c in customers], dtype=float) + 9.0)
    activity = activity / activity.sum()

    invoices: list[Invoice] = []
    for index in range(batch_size):
        customer = customers[int(rng.choice(len(customers), p=activity))]
        profile = ARCHETYPE_PROFILES[customer.archetype]

        # Invoice size varies around this customer's own average.
        amount = float(customer.avg_invoice_amount * np.exp(rng.normal(0.0, 0.45)))
        amount = _clamp(amount, 5_000.0, 6_000_000.0)

        terms = int(PAYMENT_TERMS_DAYS[int(rng.integers(len(PAYMENT_TERMS_DAYS)))])
        days_overdue = int(_clamp(float(rng.gamma(shape=2.2, scale=7.0)) + 1.0, 1.0, 120.0))
        flagged_date = as_of - timedelta(days=int(rng.integers(0, timeline_days + 1)))
        due_date = flagged_date - timedelta(days=days_overdue)
        issue_date = due_date - timedelta(days=terms)

        tier = _escalation_tier(days_overdue)
        prior_reminders = int(_clamp(float(rng.poisson(tier + 0.6)), 0.0, 8.0))
        days_since_last_contact = (
            int(_clamp(float(rng.gamma(shape=1.8, scale=4.0)), 0.0, float(days_overdue)))
            if prior_reminders > 0
            else days_overdue
        )

        has_prior_promise = bool(rng.random() < (0.18 + 0.14 * tier))
        prior_promise_kept: bool | None = None
        if has_prior_promise:
            prior_promise_kept = bool(rng.random() < profile.promise_keep_rate)

        invoices.append(
            Invoice(
                invoice_id=f"INV-{as_of.year}-{index + 1:05d}",
                customer_id=customer.customer_id,
                amount=round(amount, 2),
                issue_date=issue_date,
                due_date=due_date,
                payment_terms_days=terms,
                flagged_date=flagged_date,
                days_overdue_at_flag=days_overdue,
                current_escalation_tier=tier,
                prior_reminders_sent=prior_reminders,
                days_since_last_contact=days_since_last_contact,
                has_prior_promise=has_prior_promise,
                prior_promise_kept=prior_promise_kept,
            )
        )

    assert {inv.customer_id for inv in invoices} <= set(by_id)
    return invoices


# ---------------------------------------------------------------------------
# Outcomes -- the ground-truth label everything downstream trains against
# ---------------------------------------------------------------------------


def simulate_outcomes(
    invoices: list[Invoice],
    customers: list[Customer],
    rng: np.random.Generator,
    *,
    horizon_days: int = 30,
) -> None:
    """Sample ``recovered`` / ``recovered_date`` in place for every invoice.

    The latent model is a logistic one: an archetype base, plus observable
    history, invoice-size friction, ageing and escalation effects, plus an
    irreducible noise term. A customer who *will* pay is then given a payment
    delay; the label is whether that payment lands inside ``horizon_days`` of
    the flag date. That distinction is what separates ``RELIABLE`` from
    ``LATE_BUT_PAYS`` -- both pay, but only one pays inside the window.
    """

    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")

    by_id = {customer.customer_id: customer for customer in customers}

    for invoice in invoices:
        customer = by_id.get(invoice.customer_id)
        if customer is None:
            raise KeyError(f"No customer record for {invoice.customer_id}")

        profile = ARCHETYPE_PROFILES[customer.archetype]

        # Invoice-size friction: a bill far above this customer's norm is
        # materially harder to collect than a routine one.
        size_ratio = invoice.amount / max(customer.avg_invoice_amount, 1.0)

        # Prior promises and disputes enter as *rates*, not raw counts. Counts
        # scale with how long a customer has been on the books, so using them
        # directly would punish tenure rather than behaviour.
        history = max(customer.invoice_count, 1)
        broken_promise_rate = min(customer.prior_broken_promises_count / history, 1.0)
        dispute_rate = min(customer.prior_disputes_count / history, 1.0)

        logit = profile.recovery_logit_base
        logit += 1.40 * (customer.on_time_ratio_90d - 0.60)
        logit += 0.60 * (customer.on_time_ratio_all_time - 0.60)
        logit -= 0.020 * min(customer.avg_days_late, 60.0)
        logit -= 0.45 * float(np.log(max(size_ratio, 1e-3)))
        logit -= 0.012 * invoice.days_overdue_at_flag
        logit -= 1.30 * broken_promise_rate
        logit -= 1.00 * dispute_rate
        logit -= 0.10 * invoice.current_escalation_tier
        if invoice.has_prior_promise:
            logit += 0.55 if invoice.prior_promise_kept else -0.75
        logit += float(rng.normal(0.0, profile.outcome_noise_scale))

        p_eventual = _sigmoid(logit)
        invoice.true_recovery_probability = round(p_eventual, 6)

        will_pay = bool(rng.random() < p_eventual)
        if not will_pay:
            invoice.eventual_payment_days = None
            invoice.recovered = False
            invoice.recovered_date = None
            continue

        # How long the payment takes, given it happens at all.
        delay_scale = profile.payment_delay_mean_days * float(np.exp(rng.normal(0.0, 0.35)))
        delay = int(max(1.0, rng.exponential(delay_scale)))
        invoice.eventual_payment_days = delay

        if delay <= horizon_days:
            invoice.recovered = True
            invoice.recovered_date = invoice.flagged_date + timedelta(days=delay)
        else:
            invoice.recovered = False
            invoice.recovered_date = None


# ---------------------------------------------------------------------------
# Reply seed examples (label-first)
# ---------------------------------------------------------------------------


def _inr_group(value: int) -> str:
    """Format an integer with Indian digit grouping: 180000 -> '1,80,000'."""

    text = str(abs(value))
    if len(text) <= 3:
        grouped = text
    else:
        head, tail = text[:-3], text[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    return ("-" if value < 0 else "") + grouped


def _trim_decimal(value: float) -> str:
    """Format with up to two decimals, trimming only the fractional zeros.

    ``10.0`` renders as ``"10"``, not ``"1"`` -- naive ``rstrip("0")`` eats the
    integer part of any round multiple of ten.
    """

    text = f"{value:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _render_amount(rng: np.random.Generator, value: int) -> str:
    """Render an amount the way an Indian SMB actually writes it."""

    styles: list[str] = ["rupee_symbol", "rs_prefix", "plain_grouped"]
    if value >= 100_000 and value % 10_000 == 0:
        styles += ["lakh_short", "lakh_word"]
    if value < 100_000 and value % 1_000 == 0:
        styles += ["k_short"]

    style = styles[int(rng.integers(len(styles)))]
    if style == "rupee_symbol":
        return f"₹{_inr_group(value)}"
    if style == "rs_prefix":
        return f"Rs. {_inr_group(value)}"
    if style == "plain_grouped":
        return f"{_inr_group(value)}"
    if style == "lakh_short":
        return _trim_decimal(value / 100_000) + "L"
    if style == "lakh_word":
        return "₹" + _trim_decimal(value / 100_000) + " lakh"
    return f"{value // 1_000}k"


def _draw_amount(rng: np.random.Generator) -> int:
    """Pick a round-ish amount, the way invoices are actually quoted."""

    base = float(np.exp(rng.normal(11.3, 0.8)))
    base = _clamp(base, 8_000.0, 2_500_000.0)
    step = 10_000 if base >= 100_000 else 1_000
    return int(round(base / step) * step)


_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

_ABSOLUTE_DATE_FORMATS = ("%d %b %Y", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y")


def _next_weekday(reference: date, weekday_index: int) -> date:
    """The next occurrence of a weekday strictly after ``reference``."""

    ahead = (weekday_index - reference.weekday()) % 7
    return reference + timedelta(days=ahead or 7)


def _render_date_phrase(
    rng: np.random.Generator,
    reference: date,
    style: str,
) -> tuple[str, date]:
    """Render a date phrase plus the value it must resolve to.

    The connector ("on", "by", "within") belongs to the phrase, not the
    template, so relative and absolute forms can be dropped into the same slot
    without producing "by in 3 days".

    ``bare`` returns a phrase with no connector, for templates such as the
    Hinglish "{date} tak" that supply their own postposition.
    """

    if style == "past_explicit":
        target = reference - timedelta(days=int(rng.integers(3, 26)))
        fmt = _ABSOLUTE_DATE_FORMATS[int(rng.integers(len(_ABSOLUTE_DATE_FORMATS)))]
        return f"on {target.strftime(fmt)}", target

    if style == "explicit":
        target = reference + timedelta(days=int(rng.integers(3, 26)))
        fmt = _ABSOLUTE_DATE_FORMATS[int(rng.integers(len(_ABSOLUTE_DATE_FORMATS)))]
        connector = "on" if int(rng.integers(2)) == 0 else "by"
        return f"{connector} {target.strftime(fmt)}", target

    if style == "bare":
        choice = int(rng.integers(3))
        if choice == 0:
            return "tomorrow", reference + timedelta(days=1)
        if choice == 1:
            weekday_index = int(rng.integers(len(_WEEKDAY_NAMES)))
            target = _next_weekday(reference, weekday_index)
            return _WEEKDAY_NAMES[weekday_index], target
        target = reference + timedelta(days=int(rng.integers(3, 26)))
        fmt = _ABSOLUTE_DATE_FORMATS[int(rng.integers(len(_ABSOLUTE_DATE_FORMATS)))]
        return target.strftime(fmt), target

    # style == "relative"
    choice = int(rng.integers(4))
    if choice == 0:
        return "tomorrow", reference + timedelta(days=1)
    if choice == 1:
        days = int(rng.integers(2, 11))
        return f"in {days} days", reference + timedelta(days=days)
    if choice == 2:
        weekday_index = int(rng.integers(len(_WEEKDAY_NAMES)))
        target = _next_weekday(reference, weekday_index)
        return f"by next {_WEEKDAY_NAMES[weekday_index]}", target
    weeks = int(rng.integers(1, 4))
    return f"within {weeks} weeks", reference + timedelta(weeks=weeks)


# Public aliases: the Stage B dataset builder renders amounts and dates exactly
# the way the seed set does, so both draw on one implementation.
inr_group = _inr_group
render_amount = _render_amount
render_date_phrase = _render_date_phrase
draw_amount = _draw_amount


class _Template(BaseModel):
    """One reply template with its ground truth declared up front."""

    model_config = ConfigDict(frozen=True)

    intent: str
    tone: str
    text: str
    uses_amount: bool = False
    uses_date: bool = False
    date_style: str = "relative"
    unambiguous: bool = True


#: 5 templates per intent across the 8-class taxonomy, in the registers Indian
#: B2B correspondence actually uses: formal, curt, apologetic, annoyed, Hinglish.
REPLY_TEMPLATES: tuple[_Template, ...] = (
    # --- PROMISE_TO_PAY ---
    _Template(
        intent="PROMISE_TO_PAY",
        tone="formal",
        text="Thank you for the reminder on {invoice_id}. We will release the payment of {amount} {date}.",
        uses_amount=True,
        uses_date=True,
        date_style="explicit",
    ),
    _Template(
        intent="PROMISE_TO_PAY",
        tone="curt",
        text="Will clear {amount} {date}.",
        uses_amount=True,
        uses_date=True,
    ),
    _Template(
        intent="PROMISE_TO_PAY",
        tone="apologetic",
        text="Sorry for the delay, our collections were slow this month. Payment of {amount} will be done {date} without fail.",
        uses_amount=True,
        uses_date=True,
        date_style="explicit",
    ),
    _Template(
        intent="PROMISE_TO_PAY",
        tone="hinglish",
        text="Ji, payment {date} tak ho jayega. {amount} transfer kar denge.",
        uses_amount=True,
        uses_date=True,
        date_style="bare",
    ),
    _Template(
        intent="PROMISE_TO_PAY",
        tone="neutral",
        text="Noted on {invoice_id}. Funds of {amount} are scheduled for release {date}.",
        uses_amount=True,
        uses_date=True,
        date_style="explicit",
    ),
    # --- DISPUTE ---
    _Template(
        intent="DISPUTE",
        tone="formal",
        text="We are unable to process {invoice_id} as the quantities billed do not match our goods receipt note. Kindly share a corrected invoice.",
    ),
    _Template(
        intent="DISPUTE",
        tone="annoyed",
        text="This invoice is wrong. You have charged freight twice and we never agreed to that.",
    ),
    _Template(
        intent="DISPUTE",
        tone="curt",
        text="Amount is incorrect, we are disputing this bill. Do not send further reminders until it is corrected.",
    ),
    _Template(
        intent="DISPUTE",
        tone="neutral",
        text="There is a discrepancy in {invoice_id} - the GST rate applied is 18% but our contract says 12%. Please review.",
    ),
    _Template(
        intent="DISPUTE",
        tone="hinglish",
        text="Bill mein galti hai bhai, material short aaya tha aur poora amount charge kar diya. Please check karke revise bhejo.",
    ),
    # --- OPT_OUT ---
    _Template(
        intent="OPT_OUT",
        tone="curt",
        text="Stop messaging me. Remove this number from your list.",
    ),
    _Template(
        intent="OPT_OUT",
        tone="formal",
        text="Please unsubscribe us from these automated payment reminders. All future correspondence should go to our accounts department by post.",
    ),
    _Template(
        intent="OPT_OUT",
        tone="annoyed",
        text="Do not contact me again on WhatsApp. This is harassment.",
    ),
    _Template(
        intent="OPT_OUT",
        tone="neutral",
        text="Kindly opt us out of this reminder channel.",
    ),
    _Template(
        intent="OPT_OUT",
        tone="hinglish",
        text="Mujhe message mat bhejo, unsubscribe kar do.",
    ),
    # --- NEGOTIATION_REQUEST ---
    _Template(
        intent="NEGOTIATION_REQUEST",
        tone="curt",
        text="Will clear {amount} {date}, waive the late fee please.",
        uses_amount=True,
        uses_date=True,
        unambiguous=False,
    ),
    _Template(
        intent="NEGOTIATION_REQUEST",
        tone="formal",
        text="Cash flow is tight this quarter. Can we settle {invoice_id} in three monthly instalments instead of a single payment?",
    ),
    _Template(
        intent="NEGOTIATION_REQUEST",
        tone="apologetic",
        text="We would like to request an extension of 30 days on this invoice. Would that be acceptable?",
    ),
    _Template(
        intent="NEGOTIATION_REQUEST",
        tone="neutral",
        text="If you can offer a 5% early settlement discount we can arrange the funds this week.",
    ),
    _Template(
        intent="NEGOTIATION_REQUEST",
        tone="hinglish",
        text="Thoda time chahiye, EMI mein kar sakte hain kya? Interest mat lagana please.",
    ),
    # --- PARTIAL_PAYMENT_CLAIM ---
    _Template(
        intent="PARTIAL_PAYMENT_CLAIM",
        tone="neutral",
        text="We have already transferred {amount} against this invoice, the balance will follow shortly.",
        uses_amount=True,
    ),
    _Template(
        intent="PARTIAL_PAYMENT_CLAIM",
        tone="formal",
        text="A part payment of {amount} was remitted last week towards {invoice_id}. Kindly update your records and advise the remaining balance.",
        uses_amount=True,
    ),
    _Template(
        intent="PARTIAL_PAYMENT_CLAIM",
        tone="curt",
        text="Paid {amount} already. Rest later.",
        uses_amount=True,
    ),
    _Template(
        intent="PARTIAL_PAYMENT_CLAIM",
        tone="apologetic",
        text="We could only manage {amount} out of the total this month, sorry. The remainder is planned for next cycle.",
        uses_amount=True,
    ),
    _Template(
        intent="PARTIAL_PAYMENT_CLAIM",
        tone="hinglish",
        text="{amount} to bhej diya hai, baaki agle hafte kar denge.",
        uses_amount=True,
    ),
    # --- ALREADY_PAID_CLAIM ---
    _Template(
        intent="ALREADY_PAID_CLAIM",
        tone="curt",
        text="This is already paid. Check your bank statement.",
    ),
    _Template(
        intent="ALREADY_PAID_CLAIM",
        tone="formal",
        text="Payment against {invoice_id} was settled by NEFT {date}. The UTR reference has already been shared with your accounts team.",
        uses_date=True,
        date_style="past_explicit",
    ),
    _Template(
        intent="ALREADY_PAID_CLAIM",
        tone="annoyed",
        text="We paid this weeks ago. Please reconcile your books before sending reminders.",
    ),
    _Template(
        intent="ALREADY_PAID_CLAIM",
        tone="neutral",
        text="Our records show this invoice was cleared in full. Sharing the payment advice again for your reference.",
    ),
    _Template(
        intent="ALREADY_PAID_CLAIM",
        tone="hinglish",
        text="Payment ho chuka hai, humne pichle hafte hi transfer kiya tha.",
    ),
    # --- GENERAL_QUERY ---
    _Template(
        intent="GENERAL_QUERY",
        tone="neutral",
        text="Could you share the GST breakup for {invoice_id}?",
    ),
    _Template(
        intent="GENERAL_QUERY",
        tone="formal",
        text="Kindly confirm the bank account details to which this payment should be remitted.",
    ),
    _Template(
        intent="GENERAL_QUERY",
        tone="curt",
        text="Which PO is this invoice against?",
    ),
    _Template(
        intent="GENERAL_QUERY",
        tone="neutral",
        text="Can you resend the invoice copy? We cannot locate it in our inbox.",
    ),
    _Template(
        intent="GENERAL_QUERY",
        tone="hinglish",
        text="Invoice ki soft copy dobara bhej dena, mail mein mil nahi rahi.",
    ),
    # --- OTHER ---
    _Template(
        intent="OTHER",
        tone="neutral",
        text="Ok.",
    ),
    _Template(
        intent="OTHER",
        tone="neutral",
        text="Received, thanks.",
    ),
    _Template(
        intent="OTHER",
        tone="neutral",
        text="Our office is closed for Diwali until next week.",
    ),
    _Template(
        intent="OTHER",
        tone="curt",
        text="Wrong number.",
    ),
    _Template(
        intent="OTHER",
        tone="neutral",
        text="Please speak to Mr. Ramesh, he handles this account now.",
    ),
)


def generate_reply_seed_examples(
    rng: np.random.Generator,
    *,
    reference_date: date | None = None,
    invoice_ids: Iterable[str] | None = None,
) -> list[ReplySeedExample]:
    """Render the seed reply set: 5 label-first examples per intent class.

    This is a *sanity set* for the Phase 3 extractor and LLM baseline, not a
    training corpus. The 800-1,500 example labelled dataset for the Phase 4
    classifier is a separate effort and should not be confused with this.
    """

    reference = reference_date or date.today()
    ids = list(invoice_ids) if invoice_ids else []

    examples: list[ReplySeedExample] = []
    for index, template in enumerate(REPLY_TEMPLATES):
        invoice_id = ids[index % len(ids)] if ids else f"INV-SEED-{index + 1:03d}"

        amount_value: int | None = None
        amount_text = ""
        if template.uses_amount:
            amount_value = _draw_amount(rng)
            amount_text = _render_amount(rng, amount_value)

        date_value: date | None = None
        date_text = ""
        if template.uses_date:
            date_text, date_value = _render_date_phrase(rng, reference, template.date_style)

        text = template.text.format(
            invoice_id=invoice_id,
            amount=amount_text,
            date=date_text,
        )

        examples.append(
            ReplySeedExample(
                reply_id=f"rpl_seed_{index + 1:03d}",
                invoice_id=invoice_id,
                intent=template.intent,
                tone=template.tone,
                text=text,
                reference_date=reference,
                expected_amount=float(amount_value) if amount_value is not None else None,
                expected_date=date_value,
                amount_checkable=template.uses_amount,
                date_checkable=template.uses_date,
                unambiguous=template.unambiguous,
            )
        )

    return examples


# ---------------------------------------------------------------------------
# One-call batch
# ---------------------------------------------------------------------------


def generate_batch(
    *,
    batch_size: int = 600,
    customer_count: int | None = None,
    seed: int = 42,
    horizon_days: int = 30,
    as_of: date | None = None,
    timeline_days: int = 3,
) -> SyntheticBatch:
    """Generate a complete, reproducible batch: customers, invoices, outcomes, replies.

    The same ``seed`` and arguments always produce the same batch, because every
    draw is taken from one generator in a fixed order.
    """

    rng = make_rng(seed)
    resolved_as_of = as_of or date(2026, 9, 1)
    resolved_customers = customer_count or max(20, batch_size // 4)

    customers = generate_customers(resolved_customers, rng)
    invoices = generate_invoices(
        customers,
        rng,
        batch_size=batch_size,
        as_of=resolved_as_of,
        timeline_days=timeline_days,
    )
    simulate_outcomes(invoices, customers, rng, horizon_days=horizon_days)
    replies = generate_reply_seed_examples(
        rng,
        reference_date=resolved_as_of,
        invoice_ids=[invoice.invoice_id for invoice in invoices[:40]],
    )

    return SyntheticBatch(
        seed=seed,
        generator_version=GENERATOR_VERSION,
        horizon_days=horizon_days,
        as_of=resolved_as_of,
        timeline_days=timeline_days,
        customers=customers,
        invoices=invoices,
        reply_seed_examples=replies,
    )


def batch_summary(batch: SyntheticBatch) -> dict[str, Any]:
    """Aggregate statistics for the manifest and for eyeballing a run."""

    recovered = [inv for inv in batch.invoices if inv.recovered]
    by_archetype: dict[str, dict[str, float]] = {}
    lookup = {c.customer_id: c for c in batch.customers}

    for archetype in CustomerArchetype:
        subset = [inv for inv in batch.invoices if lookup[inv.customer_id].archetype is archetype]
        if not subset:
            continue
        by_archetype[archetype.value] = {
            "invoices": len(subset),
            "recovery_rate": round(sum(1 for inv in subset if inv.recovered) / len(subset), 4),
        }

    return {
        "customers": len(batch.customers),
        "invoices": len(batch.invoices),
        "reply_seed_examples": len(batch.reply_seed_examples),
        "recovery_rate": round(len(recovered) / max(len(batch.invoices), 1), 4),
        "archetype_counts": batch.archetype_counts(),
        "recovery_rate_by_archetype": by_archetype,
    }
