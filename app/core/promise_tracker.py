"""Promise-to-pay tracking: recording commitments and checking them.

The rule this module exists to enforce: **a promise is evidence of intent, not
evidence of payment.** A customer saying "I'll pay Friday" moves the invoice to
PROMISED and buys them quiet until Friday. It does not move money, and it never
marks an invoice paid -- only a signature-verified Razorpay webhook does that
(see ``app.api.webhooks``). Getting this backwards would let a recovery-rate
number be inflated by customers who merely said the right thing.

A promise's life:

    recorded -> (payment webhook arrives)   -> KEPT
             -> (promised date passes, no payment) -> BROKEN, escalate per ladder
             -> (customer promises again)   -> SUPERSEDED

Note what happens on a broken promise: the case escalates *one rung*, per the
pre-agreed ladder. It does not enter a retry loop, and the ladder still ends at
human handoff.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from app.core.audit import DecisionLedger, append_decision_trace
from app.models.enums import DecisionOutcome, PromiseStatus
from src.ml.schemas import IntentLabel, ReplyIntentPrediction
from src.ml.versioning import utc_now

#: Intents that can create a promise. A dispute or a general query never does,
#: however confidently it was classified.
PROMISE_BEARING_INTENTS: frozenset[IntentLabel] = frozenset(
    {IntentLabel.PROMISE_TO_PAY, IntentLabel.PARTIAL_PAYMENT_CLAIM}
)

#: Below this classifier confidence a promise is not recorded automatically.
#: The reply goes to a human instead -- recording a promise the model was
#: unsure about would silence the agent on a case that may need chasing.
MIN_PROMISE_CONFIDENCE = 0.55

#: How far ahead a promise may be dated before it stops being a commitment and
#: starts being a delaying tactic worth a human's judgement.
MAX_PROMISE_HORIZON_DAYS = 90

#: Grace period after the promised date before it counts as broken. Payments
#: settle and webhooks arrive with a lag; escalating at one minute past
#: midnight would punish customers who actually paid.
DEFAULT_GRACE_DAYS = 2


class PromiseRecord(BaseModel):
    """A tracked commitment to pay."""

    model_config = ConfigDict(protected_namespaces=())

    promise_id: str = Field(default_factory=lambda: f"PRM-{uuid.uuid4().hex[:12]}")
    invoice_id: str = Field(min_length=1)
    promised_amount: float = Field(gt=0)
    promised_date: date
    currency: str = "INR"
    source_reply_id: str | None = None
    source_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: PromiseStatus = PromiseStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None

    def is_due(self, as_of: date, *, grace_days: int = DEFAULT_GRACE_DAYS) -> bool:
        """Whether the promised date plus its grace period has passed."""

        return as_of > self.promised_date + timedelta(days=grace_days)

    def days_until_due(self, as_of: date) -> int:
        """Days remaining until the promised date; negative once it has passed."""

        return (self.promised_date - as_of).days


class PromiseOutcome(BaseModel):
    """The result of assessing one promise against reality."""

    model_config = ConfigDict(protected_namespaces=())

    promise: PromiseRecord
    status: PromiseStatus
    reason: str
    should_escalate: bool = False


class PromiseRejected(BaseModel):
    """Why a reply did not become a promise."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


def extract_promise(
    prediction: ReplyIntentPrediction,
    *,
    invoice_amount: float,
    as_of: date | None = None,
    ledger: DecisionLedger | None = None,
) -> PromiseRecord | PromiseRejected:
    """Turn a classified reply into a promise, or explain why it is not one.

    Every rejection path is audited. "The agent ignored what the customer said"
    is the kind of behaviour a judge will probe, and the answer needs to be a
    trace entry rather than a shrug.
    """

    reference = as_of or utc_now().date()

    def reject(code: str, message: str) -> PromiseRejected:
        append_decision_trace(
            invoice_id=prediction.invoice_id,
            event="promise:not_recorded",
            outcome=DecisionOutcome.SKIPPED,
            reason=message,
            ledger=ledger,
            code=code,
            intent=prediction.intent.value,
            confidence=prediction.confidence,
            reply_id=prediction.reply_id,
        )
        return PromiseRejected(code=code, message=message)

    if prediction.intent not in PROMISE_BEARING_INTENTS:
        return reject(
            "intent_not_a_promise",
            f"Intent {prediction.intent.value} does not carry a payment commitment.",
        )

    if prediction.fallback_used or prediction.confidence < MIN_PROMISE_CONFIDENCE:
        return reject(
            "low_confidence",
            (
                f"Classifier confidence {prediction.confidence:.2f} is below the "
                f"{MIN_PROMISE_CONFIDENCE:.2f} threshold for recording a promise "
                "without review."
            ),
        )

    promised_date = prediction.entities.promised_date
    if promised_date is None:
        return reject(
            "no_date",
            "Reply committed to paying but named no date; cannot track it as a promise.",
        )

    if promised_date < reference:
        return reject(
            "date_in_past",
            f"Promised date {promised_date.isoformat()} is already in the past.",
        )

    if (promised_date - reference).days > MAX_PROMISE_HORIZON_DAYS:
        return reject(
            "date_too_far",
            (
                f"Promised date {promised_date.isoformat()} is more than "
                f"{MAX_PROMISE_HORIZON_DAYS} days out; routing to a human."
            ),
        )

    amount = prediction.entities.promised_amount
    if amount is None or amount <= 0:
        # A promise with no amount named is a promise to pay the invoice.
        amount = invoice_amount
    # A customer cannot promise more than they owe; treat an over-large figure
    # as an extraction error rather than propagating it into the numbers.
    amount = min(float(amount), invoice_amount)

    promise = PromiseRecord(
        invoice_id=prediction.invoice_id,
        promised_amount=amount,
        promised_date=promised_date,
        currency=prediction.entities.currency,
        source_reply_id=prediction.reply_id,
        source_confidence=prediction.confidence,
    )

    append_decision_trace(
        invoice_id=prediction.invoice_id,
        event="promise:recorded",
        outcome=DecisionOutcome.APPROVED,
        reason=(
            f"Recorded promise of {promise.currency} {promise.promised_amount:,.2f} "
            f"by {promise.promised_date.isoformat()}."
        ),
        ledger=ledger,
        promise_id=promise.promise_id,
        reply_id=prediction.reply_id,
        confidence=prediction.confidence,
        is_partial=amount < invoice_amount,
    )
    return promise


def assess_promise(
    promise: PromiseRecord,
    *,
    amount_paid: float,
    as_of: date | None = None,
    grace_days: int = DEFAULT_GRACE_DAYS,
    ledger: DecisionLedger | None = None,
) -> PromiseOutcome:
    """Decide whether a promise was kept, broken, or is still running.

    ``amount_paid`` must come from settled payments -- the webhook receiver is
    the only writer of that field. This function does not ask the customer.
    """

    reference = as_of or utc_now().date()

    if promise.status is not PromiseStatus.PENDING:
        return PromiseOutcome(
            promise=promise,
            status=promise.status,
            reason=f"Promise already resolved as {promise.status.value}.",
        )

    if amount_paid + 0.01 >= promise.promised_amount:
        resolved = promise.model_copy(
            update={"status": PromiseStatus.KEPT, "resolved_at": utc_now()}
        )
        append_decision_trace(
            invoice_id=promise.invoice_id,
            event="promise:kept",
            outcome=DecisionOutcome.APPROVED,
            reason=(
                f"Payment of {amount_paid:,.2f} settled the promised "
                f"{promise.promised_amount:,.2f}."
            ),
            ledger=ledger,
            promise_id=promise.promise_id,
        )
        return PromiseOutcome(
            promise=resolved,
            status=PromiseStatus.KEPT,
            reason="Payment confirmed at or above the promised amount.",
        )

    if not promise.is_due(reference, grace_days=grace_days):
        remaining = promise.days_until_due(reference)
        return PromiseOutcome(
            promise=promise,
            status=PromiseStatus.PENDING,
            reason=(
                f"Promise is not yet due ({remaining} days to "
                f"{promise.promised_date.isoformat()}, plus {grace_days} days grace)."
            ),
        )

    broken = promise.model_copy(update={"status": PromiseStatus.BROKEN, "resolved_at": utc_now()})
    append_decision_trace(
        invoice_id=promise.invoice_id,
        event="promise:broken",
        outcome=DecisionOutcome.BLOCKED,
        reason=(
            f"Promised {promise.currency} {promise.promised_amount:,.2f} by "
            f"{promise.promised_date.isoformat()}; {amount_paid:,.2f} received by "
            f"{reference.isoformat()}."
        ),
        ledger=ledger,
        promise_id=promise.promise_id,
        grace_days=grace_days,
    )
    return PromiseOutcome(
        promise=broken,
        status=PromiseStatus.BROKEN,
        reason="Promised date and grace period passed without full payment.",
        # One rung, per the pre-agreed ladder -- not a retry loop.
        should_escalate=True,
    )


def supersede(
    previous: PromiseRecord,
    replacement: PromiseRecord,
    *,
    ledger: DecisionLedger | None = None,
) -> PromiseRecord:
    """Mark an earlier promise superseded by a newer one.

    Kept as an explicit state rather than deleting the old row: "this customer
    has rescheduled three times" is exactly the pattern a human reviewer needs
    to see, and it disappears if renegotiation overwrites history.
    """

    retired = previous.model_copy(
        update={"status": PromiseStatus.SUPERSEDED, "resolved_at": utc_now()}
    )
    append_decision_trace(
        invoice_id=previous.invoice_id,
        event="promise:superseded",
        outcome=DecisionOutcome.APPROVED,
        reason=(
            f"Promise {previous.promise_id} ({previous.promised_date.isoformat()}) replaced by "
            f"{replacement.promise_id} ({replacement.promised_date.isoformat()})."
        ),
        ledger=ledger,
        previous_promise_id=previous.promise_id,
        replacement_promise_id=replacement.promise_id,
    )
    return retired
