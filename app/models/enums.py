"""Enumerations shared by the ORM models, the core logic and the API.

These live in their own module because both the persistence layer and the pure
core import them. Putting them next to the SQLAlchemy models would force the
core to import SQLAlchemy just to name a state.
"""

from enum import Enum


class InvoiceStatus(str, Enum):
    """Where an invoice sits in its collection lifecycle."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PROMISED = "PROMISED"
    PAID = "PAID"
    DISPUTED = "DISPUTED"
    WRITTEN_OFF = "WRITTEN_OFF"
    HANDED_OFF = "HANDED_OFF"


class EscalationState(str, Enum):
    """The escalation ladder's states.

    The values match the state names registered with the ``transitions``
    machine in :mod:`app.core.escalation` exactly -- the FSM is the authority
    on which of these an invoice may occupy, and this enum is how that state is
    persisted and returned over the API.
    """

    MONITORING = "monitoring"
    REMINDED = "reminded"
    ESCALATED = "escalated"
    HUMAN_HANDOFF = "human_handoff"
    CLOSED = "closed"


class InterventionTier(str, Enum):
    """What the scorer recommends doing about an invoice this cycle."""

    WAIT = "WAIT"
    REMIND = "REMIND"
    ESCALATE = "ESCALATE"


class PromiseStatus(str, Enum):
    """Lifecycle of a recorded promise to pay."""

    PENDING = "PENDING"
    KEPT = "KEPT"
    BROKEN = "BROKEN"
    SUPERSEDED = "SUPERSEDED"


class ContactChannel(str, Enum):
    """Delivery channels the agent may use.

    WhatsApp is declared but not wired for the buildathon -- see the README's
    out-of-scope section. Declaring it keeps the opt-out registry channel-aware
    from day one rather than retrofitting it later.
    """

    EMAIL = "email"
    WHATSAPP = "whatsapp"


class DecisionOutcome(str, Enum):
    """How a policy-gated decision resolved."""

    APPROVED = "approved"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    EXECUTED = "executed"
    FAILED = "failed"
