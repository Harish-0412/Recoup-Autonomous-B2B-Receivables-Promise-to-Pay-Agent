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


class ReplyDisposition(str, Enum):
    """What was done with one inbound customer reply.

    The distinction that matters is ``NEEDS_REVIEW``. A classifier that is
    unsure must hand the reply to a person rather than guessing, because the
    two failure modes are not symmetric: inventing a promise that the customer
    never made stops the agent chasing a live debt, and mis-reading an
    unsubscribe keeps mailing someone who asked you to stop.
    """

    #: Classified confidently and acted on without a human.
    AUTO_HANDLED = "auto_handled"
    #: Queued for a person. Nothing was recorded from it.
    NEEDS_REVIEW = "needs_review"
    #: A person has since dealt with it.
    REVIEWED = "reviewed"


class DeliveryStatus(str, Enum):
    """What actually happened to one outbound message.

    The distinction this enum exists to make: a ``ContactLog`` row used to mean
    "we contacted them", when in fact nothing had been sent. Only ``SENT`` and
    ``SIMULATED`` count as contact for the frequency and volume caps.
    ``FAILED`` rows are kept because a failed send is worth seeing, but they
    must never consume a customer's contact budget or advance the ladder --
    otherwise a provider outage silently exhausts the book.
    """

    #: Handed to the provider and acknowledged with a message ID.
    SENT = "sent"
    #: DRY_RUN was on. Fully rendered, deliberately not delivered. Counts as
    #: contact so a dry run exercises the same caps a real run would.
    SIMULATED = "simulated"
    #: The provider rejected it or was unreachable. Does not count as contact.
    FAILED = "failed"


class DecisionOutcome(str, Enum):
    """How a policy-gated decision resolved."""

    APPROVED = "approved"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    EXECUTED = "executed"
    FAILED = "failed"


class AllocationSource(str, Enum):
    """Where a payment allocation originated.

    ``razorpay_link`` and ``razorpay_payment`` are both Razorpay events but
    represent different Razorpay event types (payment_link.paid vs
    payment.captured). Keying by source+provider_ref makes it impossible for
    both events to count the same money twice -- the unique constraint on
    (business_id, source, provider_ref) rejects the duplicate at the DB level.

    ``bank_utr`` is an operator-entered NEFT/RTGS/UPI transfer.
    ``erp_credit_note`` is a negative amount from an ERP sync (Zoho/QBO credit
    note that partially offsets what the customer owes).
    """

    RAZORPAY_LINK = "razorpay_link"
    RAZORPAY_PAYMENT = "razorpay_payment"
    BANK_UTR = "bank_utr"
    ERP_CREDIT_NOTE = "erp_credit_note"


class IntegrationProvider(str, Enum):
    """ERP or accounting systems Recoup can pull invoices from.

    Each provider has exactly one ``IntegrationCredential`` row per tenant.
    Tally has no cloud API, so its "integration" is a file upload; it still
    gets a provider value so import history is query-able.
    """

    ZOHO_BOOKS = "zoho_books"
    QUICKBOOKS = "quickbooks"
    RAZORPAY_INVOICES = "razorpay_invoices"
    TALLY = "tally"
