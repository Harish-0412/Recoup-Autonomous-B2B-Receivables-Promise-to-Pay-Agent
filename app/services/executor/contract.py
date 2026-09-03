"""The executor's inputs and outputs, as types that cannot express a mistake.

The safety property this module enforces is the one the whole agent rests on:

    score -> propose -> **gate** -> transition -> execute

Nothing between "propose" and "execute" may skip the gate. That is a comment in
``app.core.agent``, and a comment is not a mechanism. Here it becomes one:
:class:`ExecutionIntent` is the executor's only input type, it can only be built
from a :class:`~app.core.policy.PolicyDecision`, and its validator rejects a
decision that is not ``allowed``. An executor handed a bare ``ProposedAction``
does not fail at runtime -- there is no way to call it at all, because no
``ExecutionIntent`` can be constructed from one.

Two smaller invariants ride along:

* An intent for a non-contacting action (``HAND_OFF``, ``CLOSE``) is rejected
  too. Those are internal state moves; routing one into the send path would put
  a "your case has been handed to a human" email in front of a customer.
* The discount an intent carries is the gate's ``effective_discount_pct``,
  never the action's ``requested_discount_pct``. The gate is allowed to lower a
  request, and the message that goes out must quote the number that survived
  the gate, not the one the agent asked for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.domain import CaseSnapshot
from app.core.policy import CONTACTING_ACTIONS, ActionType, PolicyDecision, ProposedAction
from app.models.enums import ContactChannel, DeliveryStatus
from src.ml.versioning import utc_now


class ExecutionIntent(BaseModel):
    """An approved action, ready to be delivered. Frozen and self-validating.

    Build it with :meth:`from_decision`; the validator runs either way, so
    constructing it directly with an unapproved decision raises just the same.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    invoice_id: str = Field(min_length=1)
    case: CaseSnapshot
    action: ProposedAction
    decision: PolicyDecision

    @property
    def channel(self) -> ContactChannel:
        return self.action.channel

    @property
    def ladder_step(self) -> str:
        return self.action.ladder_step

    @property
    def action_type(self) -> ActionType:
        return self.action.action_type

    @property
    def discount_pct(self) -> float:
        """The discount the *gate* allowed, which may be less than requested."""

        return self.decision.effective_discount_pct

    @property
    def outstanding(self) -> float:
        return self.case.invoice.outstanding

    @property
    def payable_amount(self) -> float:
        """What the payment link should ask for, after any approved discount."""

        return round(self.outstanding * (1.0 - self.discount_pct / 100.0), 2)

    @property
    def recipient(self) -> str | None:
        return self.case.customer.email

    @model_validator(mode="after")
    def _must_be_approved_contact(self) -> Self:
        if not self.decision.allowed:
            raise ValueError(
                f"Refusing to build an ExecutionIntent for {self.invoice_id}: the "
                f"policy gate did not approve it ({self.decision.reason}). "
                "Execution is only reachable through an approved decision."
            )
        if self.action.action_type not in CONTACTING_ACTIONS:
            raise ValueError(
                f"{self.action.action_type.value} is not a contacting action; it "
                "moves internal state and must never reach the send path."
            )
        # A decision approving a different invoice than the case describes is a
        # wiring bug that would mail the wrong customer. Cheap to rule out.
        if self.decision.invoice_id != self.invoice_id:
            raise ValueError(
                f"Decision is for {self.decision.invoice_id} but the intent is for "
                f"{self.invoice_id}."
            )
        if self.action.invoice_id != self.invoice_id:
            raise ValueError(
                f"Action is for {self.action.invoice_id} but the intent is for "
                f"{self.invoice_id}."
            )
        return self

    @classmethod
    def from_decision(
        cls,
        case: CaseSnapshot,
        action: ProposedAction,
        decision: PolicyDecision,
    ) -> Self:
        """The intended entry point. Raises unless the gate approved a contact."""

        return cls(
            invoice_id=case.invoice.invoice_id,
            case=case,
            action=action,
            decision=decision,
        )


class RenderedMessage(BaseModel):
    """A message, fully composed, before anything is sent.

    Rendering is separated from sending so the dry run can produce the exact
    bytes a real send would, and so tests can assert on copy without a provider
    in the loop.
    """

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    html: str = Field(min_length=1)
    text: str = Field(min_length=1)

    def preview(self, limit: int = 500) -> str:
        return self.text[:limit]


class PaymentLink(BaseModel):
    """A Razorpay payment link, or the record of not needing a new one."""

    model_config = ConfigDict(frozen=True)

    link_id: str
    url: str
    amount: float
    #: True when an existing unpaid link was reused rather than a new one
    #: created. Every cycle minting a fresh link would leave a customer holding
    #: several live links for one invoice, any of which could be paid.
    reused: bool = False


class ExecutionResult(BaseModel):
    """What the executor did, in enough detail to audit and to retry.

    ``status`` drives the caller: only a delivered result may advance the
    ladder. The failure case deliberately carries no exception -- the executor
    converts provider failures into a value, because one unreachable provider
    must not end a batch.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    invoice_id: str
    status: DeliveryStatus
    channel: ContactChannel
    ladder_step: str

    subject: str = ""
    body_preview: str = ""
    provider_message_id: str | None = None
    payment_link_id: str | None = None
    payment_link_url: str | None = None
    payment_link_reused: bool = False
    amount_requested: float = 0.0

    error: str | None = None
    executed_at: datetime = Field(default_factory=utc_now)

    @property
    def delivered(self) -> bool:
        """Whether this counts as contact and may advance the ladder.

        ``SIMULATED`` is included on purpose: a dry run is meant to behave
        exactly like a real run apart from the network call, so it exercises
        the same caps and the same ladder movement. The row is labelled, so a
        report can always exclude it.
        """

        return self.status in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED)

    @classmethod
    def failure(
        cls,
        intent: ExecutionIntent,
        error: str,
        *,
        subject: str = "",
        body_preview: str = "",
        payment_link_id: str | None = None,
    ) -> ExecutionResult:
        return cls(
            invoice_id=intent.invoice_id,
            status=DeliveryStatus.FAILED,
            channel=intent.channel,
            ladder_step=intent.ladder_step,
            subject=subject,
            body_preview=body_preview,
            payment_link_id=payment_link_id,
            amount_requested=intent.payable_amount,
            error=error[:2000],
        )
