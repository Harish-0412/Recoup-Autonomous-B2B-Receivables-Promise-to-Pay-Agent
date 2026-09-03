"""The policy engine: the single gate every outbound action must pass.

The architecture calls this "propose, then dispose". The scorer and the
escalation machine *propose* an action; nothing sends it until this module
disposes of it. There is no bypass, and no caller is trusted to have checked
the caps itself.

**Why ``business-rules`` rather than a stack of ifs.** The rules are data --
:func:`build_policy_rules` turns a ``PolicyConfig`` into a list of
condition/action dicts, which ``business_rules.run_all`` evaluates. That means
the configured ceilings and caps are inspectable (``GET /api/v1/policy``
returns them), diffable, and adjustable without editing branching logic. A
hand-rolled ``if`` chain would put the business owner's discount ceiling inside
a function body where nobody can see it.

**What OPA would give us that this does not.** Open Policy Agent is the
industry-standard version of this pattern: Rego rules, a separate decision
service, decision logs as a first-class artifact. It is the documented next
step in ``docs/architecture.md``. Not deployed here because a second service in
Go is not a buildathon-sized dependency -- the pattern is the same either way.

Two invariants this module holds:

* **Blocks are absolute; adjustments are silent corrections.** A violation
  means the action does not happen. An adjustment (only the discount clamp)
  means it happens with a bounded value. Nothing here can *raise* a ceiling.
* **Every decision is recorded.** :func:`evaluate_action` writes one Decision
  Trace entry per evaluation, whichever way it goes, so a blocked action leaves
  as much evidence as a sent one.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from business_rules import run_all
from business_rules.actions import BaseActions, rule_action
from business_rules.fields import FIELD_NUMERIC, FIELD_TEXT
from business_rules.variables import (
    BaseVariables,
    boolean_rule_variable,
    numeric_rule_variable,
    string_rule_variable,
)
from pydantic import BaseModel, ConfigDict, Field

from app.core.audit import DecisionLedger, append_decision_trace
from app.core.config import get_settings
from app.core.domain import CaseSnapshot
from app.models.enums import ContactChannel, DecisionOutcome

DEFAULT_ESCALATION_LADDER: tuple[str, ...] = (
    "reminder_1",
    "reminder_2",
    "final_notice",
    "human_handoff",
)


class ActionType(str, Enum):
    """What the agent is proposing to do."""

    SEND_REMINDER = "SEND_REMINDER"
    ESCALATE = "ESCALATE"
    OFFER_SETTLEMENT = "OFFER_SETTLEMENT"
    HAND_OFF = "HAND_OFF"
    CLOSE = "CLOSE"


#: Actions that put a message in front of a customer. These are the ones the
#: contact caps and the opt-out registry apply to; an internal handoff or a
#: case closure is not a contact and must not be blocked by a contact rule.
CONTACTING_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.SEND_REMINDER, ActionType.ESCALATE, ActionType.OFFER_SETTLEMENT}
)


class PolicyConfig(BaseModel):
    """The business owner's configured limits.

    Every number here is a ceiling the agent may approach but never exceed. The
    LLM never sees these as suggestions -- it does not participate in this
    decision at all.
    """

    model_config = ConfigDict(protected_namespaces=())

    #: Largest settlement discount, as a percentage of the invoice.
    discount_ceiling_pct: float = Field(default=10.0, ge=0.0, le=100.0)
    #: Absolute rupee ceiling, applied on top of the percentage. ``None``
    #: means the percentage alone governs.
    max_discount_amount: float | None = Field(default=None, ge=0.0)

    #: Minimum days between two contacts about the same invoice.
    min_contact_gap_days: int = Field(default=3, ge=0)
    #: Total messages the agent may send about one invoice, ever.
    max_contacts_per_invoice: int = Field(default=4, ge=0)
    #: Do not contact until an invoice is at least this far overdue.
    min_days_overdue_to_contact: int = Field(default=1, ge=0)
    #: While a promise is open and not yet due, stay quiet. Chasing a customer
    #: who has already committed to a date is the fastest way to lose them.
    quiet_while_promise_open: bool = True

    escalation_ladder: tuple[str, ...] = DEFAULT_ESCALATION_LADDER
    #: Days an opt-out is honoured before the customer may be contacted again.
    optout_days: int = Field(default=30, ge=1)

    @property
    def ladder_length(self) -> int:
        return len(self.escalation_ladder)

    def step_name(self, index: int) -> str:
        """The ladder step at ``index``, saturating at the final step."""

        if not self.escalation_ladder:
            return "human_handoff"
        return self.escalation_ladder[min(index, self.ladder_length - 1)]

    def is_ladder_exhausted(self, index: int) -> bool:
        """Whether ``index`` has run past the last configured step."""

        return index >= self.ladder_length - 1


class ProposedAction(BaseModel):
    """One action the agent wants to take, before the gate sees it."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str = Field(min_length=1)
    action_type: ActionType
    channel: ContactChannel = ContactChannel.EMAIL
    ladder_step: str = "reminder_1"
    #: Requested discount. The gate may lower this; it can never raise it.
    requested_discount_pct: float = Field(default=0.0, ge=0.0)
    rationale: str = ""

    @property
    def is_contact(self) -> bool:
        return self.action_type in CONTACTING_ACTIONS


class PolicyViolation(BaseModel):
    """A rule that blocked the action."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class PolicyAdjustment(BaseModel):
    """A value the gate lowered rather than rejecting outright."""

    model_config = ConfigDict(frozen=True)

    field: str
    requested: float
    allowed: float
    reason: str


class PolicyDecision(BaseModel):
    """The gate's verdict on one proposed action."""

    model_config = ConfigDict(protected_namespaces=())

    invoice_id: str
    action_type: ActionType
    allowed: bool
    violations: list[PolicyViolation] = Field(default_factory=list)
    adjustments: list[PolicyAdjustment] = Field(default_factory=list)
    effective_discount_pct: float = 0.0
    effective_discount_amount: float = 0.0

    @property
    def outcome(self) -> DecisionOutcome:
        return DecisionOutcome.APPROVED if self.allowed else DecisionOutcome.BLOCKED

    @property
    def reason(self) -> str:
        """A one-line explanation, suitable for the audit trail and the API."""

        if self.allowed:
            if self.adjustments:
                return "Approved with adjustments: " + "; ".join(
                    f"{a.field} {a.requested:g} -> {a.allowed:g} ({a.reason})"
                    for a in self.adjustments
                )
            return "Approved: no policy rule blocked this action."
        return "Blocked: " + "; ".join(v.message for v in self.violations)


# ---------------------------------------------------------------------------
# business-rules bindings
# ---------------------------------------------------------------------------


class PolicyVariables(BaseVariables):
    """The facts a policy rule may test.

    Each method is one named fact. ``business_rules`` looks these up by the
    ``name`` in a rule's condition, so this class is effectively the schema of
    what a rule is allowed to know.
    """

    def __init__(self, case: CaseSnapshot, action: ProposedAction, config: PolicyConfig) -> None:
        self.case = case
        self.action = action
        self.config = config

    # --- contact history -------------------------------------------------
    @numeric_rule_variable(label="Days since the last contact on this invoice")
    def days_since_last_contact(self) -> float:
        return float(self.case.invoice.days_since_last_contact)

    @numeric_rule_variable(label="Messages already sent about this invoice")
    def contacts_sent(self) -> float:
        return float(self.case.invoice.prior_reminders_sent)

    @numeric_rule_variable(label="Days the invoice is overdue")
    def days_overdue(self) -> float:
        return float(self.case.invoice.days_overdue)

    # --- standing instructions -------------------------------------------
    @boolean_rule_variable(label="Customer has opted out of this channel")
    def is_opted_out(self) -> bool:
        return self.case.is_opted_out(self.action.channel)

    @boolean_rule_variable(label="An undue promise to pay is open")
    def has_open_undue_promise(self) -> bool:
        invoice = self.case.invoice
        if not invoice.has_open_promise:
            return False
        due_in = invoice.open_promise_due_in_days
        # A promise whose date has passed is no longer a reason to stay quiet.
        return due_in is None or due_in >= 0

    # --- the action itself -------------------------------------------------
    @boolean_rule_variable(label="This action puts a message in front of a customer")
    def is_contact_action(self) -> bool:
        return self.action.is_contact

    @string_rule_variable(label="Proposed action type")
    def action_type(self) -> str:
        return self.action.action_type.value

    @numeric_rule_variable(label="Requested discount percentage")
    def requested_discount_pct(self) -> float:
        return float(self.action.requested_discount_pct)

    @numeric_rule_variable(label="Position in the escalation ladder")
    def ladder_index(self) -> float:
        return float(self.case.invoice.ladder_index)


class PolicyActions(BaseActions):
    """What a fired rule may do: block, or clamp a number downward.

    Note what is absent -- there is no action that approves an action, raises a
    ceiling, or sends anything. A rule's only powers are to refuse and to
    reduce.
    """

    def __init__(self, action: ProposedAction, config: PolicyConfig) -> None:
        self.action = action
        self.config = config
        self.violations: list[PolicyViolation] = []
        self.adjustments: list[PolicyAdjustment] = []

    @rule_action(params={"code": FIELD_TEXT, "message": FIELD_TEXT})
    def block(self, code: str, message: str) -> None:
        self.violations.append(PolicyViolation(code=code, message=message))

    @rule_action(params={"ceiling": FIELD_NUMERIC, "reason": FIELD_TEXT})
    def clamp_discount(self, ceiling: float, reason: str) -> None:
        requested = float(self.action.requested_discount_pct)
        allowed = min(requested, float(ceiling))
        if allowed < requested:
            self.adjustments.append(
                PolicyAdjustment(
                    field="discount_pct",
                    requested=requested,
                    allowed=allowed,
                    reason=reason,
                )
            )


def build_policy_rules(config: PolicyConfig) -> list[dict[str, Any]]:
    """Compile a ``PolicyConfig`` into ``business_rules`` rule data.

    This is the whole point of the library: the returned list *is* the policy,
    as inspectable data. ``GET /api/v1/policy`` serves it verbatim, so what the
    gate enforces and what the API claims it enforces cannot drift apart.
    """

    contact_only = {"name": "is_contact_action", "operator": "is_true", "value": True}

    rules: list[dict[str, Any]] = [
        {
            "conditions": {
                "all": [
                    contact_only,
                    {"name": "is_opted_out", "operator": "is_true", "value": True},
                ]
            },
            "actions": [
                {
                    "name": "block",
                    "params": {
                        "code": "opt_out",
                        "message": "Customer has opted out of contact on this channel.",
                    },
                }
            ],
        },
        {
            "conditions": {
                "all": [
                    contact_only,
                    {
                        "name": "days_since_last_contact",
                        "operator": "less_than",
                        "value": config.min_contact_gap_days,
                    },
                ]
            },
            "actions": [
                {
                    "name": "block",
                    "params": {
                        "code": "contact_frequency_cap",
                        "message": (
                            f"Last contact was under {config.min_contact_gap_days} "
                            "days ago; minimum gap not met."
                        ),
                    },
                }
            ],
        },
        {
            "conditions": {
                "all": [
                    contact_only,
                    {
                        "name": "contacts_sent",
                        "operator": "greater_than_or_equal_to",
                        "value": config.max_contacts_per_invoice,
                    },
                ]
            },
            "actions": [
                {
                    "name": "block",
                    "params": {
                        "code": "contact_volume_cap",
                        "message": (
                            f"Already sent {config.max_contacts_per_invoice} messages "
                            "about this invoice; cap reached."
                        ),
                    },
                }
            ],
        },
        {
            "conditions": {
                "all": [
                    contact_only,
                    {
                        "name": "days_overdue",
                        "operator": "less_than",
                        "value": config.min_days_overdue_to_contact,
                    },
                ]
            },
            "actions": [
                {
                    "name": "block",
                    "params": {
                        "code": "not_yet_overdue",
                        "message": (
                            "Invoice is not yet overdue enough to warrant contact "
                            f"({config.min_days_overdue_to_contact}-day threshold)."
                        ),
                    },
                }
            ],
        },
        {
            "conditions": {
                "all": [
                    {
                        "name": "requested_discount_pct",
                        "operator": "greater_than",
                        "value": config.discount_ceiling_pct,
                    }
                ]
            },
            "actions": [
                {
                    "name": "clamp_discount",
                    "params": {
                        "ceiling": config.discount_ceiling_pct,
                        "reason": (f"configured ceiling is {config.discount_ceiling_pct:g}%"),
                    },
                }
            ],
        },
    ]

    if config.quiet_while_promise_open:
        rules.append(
            {
                "conditions": {
                    "all": [
                        contact_only,
                        {"name": "has_open_undue_promise", "operator": "is_true", "value": True},
                        # An escalation after a promise breaks is a different
                        # decision, made by the promise tracker, and is not
                        # silenced by the promise it is reacting to.
                        {"name": "action_type", "operator": "equal_to", "value": "SEND_REMINDER"},
                    ]
                },
                "actions": [
                    {
                        "name": "block",
                        "params": {
                            "code": "promise_open",
                            "message": (
                                "An undue promise to pay is open; staying quiet "
                                "until its date passes."
                            ),
                        },
                    }
                ],
            }
        )

    return rules


class PolicyEngine:
    """Evaluates proposed actions against the configured policy.

    Also answers the two guard questions the escalation state machine asks --
    :meth:`is_contact_allowed` and :meth:`escalation_step_due` -- so that the
    FSM's guards and the outbound gate cannot disagree about what is permitted.
    """

    def __init__(
        self,
        config: PolicyConfig | None = None,
        *,
        ledger: DecisionLedger | None = None,
    ) -> None:
        self.config = config or PolicyConfig()
        self.ledger = ledger
        self._rules = build_policy_rules(self.config)

    @property
    def rules(self) -> list[dict[str, Any]]:
        """The compiled rule data, as served by ``GET /api/v1/policy``."""

        return self._rules

    def evaluate(self, case: CaseSnapshot, action: ProposedAction) -> PolicyDecision:
        """Run every rule and return the verdict. Does not write to the ledger."""

        variables = PolicyVariables(case, action, self.config)
        actions = PolicyActions(action, self.config)

        # stop_on_first_trigger=False on purpose: we want *every* reason an
        # action was refused, not just the first. A collections decision that
        # says "blocked by the contact cap" when it was also an opt-out is a
        # misleading audit record.
        run_all(
            rule_list=self._rules,
            defined_variables=variables,
            defined_actions=actions,
            stop_on_first_trigger=False,
        )

        effective_pct = action.requested_discount_pct
        for adjustment in actions.adjustments:
            if adjustment.field == "discount_pct":
                effective_pct = min(effective_pct, adjustment.allowed)

        # The absolute rupee ceiling is applied after the percentage rule,
        # because it depends on this invoice's amount and so cannot be
        # expressed as a static value in the rule data.
        outstanding = case.invoice.outstanding
        effective_amount = outstanding * effective_pct / 100.0
        cap = self.config.max_discount_amount
        if cap is not None and effective_amount > cap:
            capped_pct = (cap / outstanding * 100.0) if outstanding > 0 else 0.0
            actions.adjustments.append(
                PolicyAdjustment(
                    field="discount_pct",
                    requested=effective_pct,
                    allowed=capped_pct,
                    reason=f"absolute discount ceiling is {cap:g}",
                )
            )
            effective_pct = capped_pct
            effective_amount = cap

        return PolicyDecision(
            invoice_id=action.invoice_id,
            action_type=action.action_type,
            allowed=not actions.violations,
            violations=actions.violations,
            adjustments=actions.adjustments,
            effective_discount_pct=round(effective_pct, 4),
            effective_discount_amount=round(effective_amount, 2),
        )

    def evaluate_action(
        self,
        case: CaseSnapshot,
        action: ProposedAction,
        *,
        ledger: DecisionLedger | None = None,
    ) -> PolicyDecision:
        """Evaluate, then record the verdict in the Decision Trace.

        This is the method callers should use. :meth:`evaluate` exists for the
        FSM guards, which ask the same questions many times per cycle and must
        not fill the ledger with one entry per guard check.
        """

        decision = self.evaluate(case, action)
        append_decision_trace(
            invoice_id=action.invoice_id,
            event=f"policy:{action.action_type.value.lower()}",
            outcome=decision.outcome,
            reason=decision.reason,
            ledger=ledger if ledger is not None else self.ledger,
            action_type=action.action_type.value,
            channel=action.channel.value,
            ladder_step=action.ladder_step,
            violations=[v.code for v in decision.violations],
            effective_discount_pct=decision.effective_discount_pct,
        )
        return decision

    # --- guards used by the escalation state machine ----------------------

    def evaluate_contact_probe(
        self,
        case: CaseSnapshot,
        *,
        channel: ContactChannel | None = None,
        action_type: ActionType = ActionType.SEND_REMINDER,
    ) -> PolicyDecision:
        """Ask the gate whether a contact would be permitted, without sending.

        Returns the full decision rather than a boolean so the state machine
        can put the *reason* a transition was refused into the audit trail --
        "blocked" on its own is not an explanation anyone can act on.
        """

        probe = ProposedAction(
            invoice_id=case.invoice_id,
            action_type=action_type,
            channel=channel or case.customer.preferred_channel,
            ladder_step=self.config.step_name(case.invoice.ladder_index),
        )
        return self.evaluate(case, probe)

    def is_contact_allowed(
        self,
        case: CaseSnapshot,
        *,
        channel: ContactChannel | None = None,
        action_type: ActionType = ActionType.SEND_REMINDER,
    ) -> bool:
        """Whether a message may be sent about this case right now.

        Delegates to the same rule set the outbound gate uses, so the state
        machine cannot move a case into a state whose action would then be
        refused.
        """

        return self.evaluate_contact_probe(case, channel=channel, action_type=action_type).allowed

    def escalation_step_due(self, case: CaseSnapshot, ladder_index: int) -> bool:
        """Whether the case has waited long enough for the next ladder rung.

        Each rung requires the configured contact gap to have elapsed, and the
        ladder cannot advance past its final configured step -- that is what
        makes "the agent stops" a property of the configuration rather than a
        promise in a README.
        """

        if ladder_index >= self.config.ladder_length:
            return False
        return case.invoice.days_since_last_contact >= self.config.min_contact_gap_days

    def describe(self) -> dict[str, Any]:
        """A JSON-serialisable view of the active policy, for the API."""

        return {
            "config": json.loads(self.config.model_dump_json()),
            "rules": self._rules,
            "rule_count": len(self._rules),
        }


def policy_config_from_settings() -> PolicyConfig:
    """Build the active policy from application settings.

    The single place environment configuration becomes a ``PolicyConfig``, so
    the API, the scheduler and any future worker all gate against identical
    ceilings. A component that constructed its own defaults instead would be a
    component that silently enforces a different policy.
    """

    settings = get_settings()
    return PolicyConfig(
        discount_ceiling_pct=float(settings.DEFAULT_DISCOUNT_CEILING_PCT),
        max_discount_amount=settings.DEFAULT_MAX_DISCOUNT_AMOUNT,
        min_contact_gap_days=settings.DEFAULT_MIN_CONTACT_GAP_DAYS,
        max_contacts_per_invoice=settings.DEFAULT_MAX_CONTACTS_PER_INVOICE,
        min_days_overdue_to_contact=settings.DEFAULT_MIN_DAYS_OVERDUE_TO_CONTACT,
        escalation_ladder=tuple(settings.escalation_ladder),
        optout_days=settings.DEFAULT_OPTOUT_DAYS,
    )
