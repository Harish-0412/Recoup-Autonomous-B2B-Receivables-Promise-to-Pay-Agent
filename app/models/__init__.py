"""ORM models and the enums shared across the persistence, core and API layers.

Import from here rather than from ``app.models.tables`` so call sites stay
stable if a table moves. Importing this package also registers every model on
``Base.metadata``, which is what makes Alembic's autogenerate see
them -- ``app.db.session`` deliberately does not import models itself, to avoid
a circular import.
"""

from app.models.enums import (
    ContactChannel,
    DecisionOutcome,
    DeliveryStatus,
    EscalationState,
    InterventionTier,
    InvoiceStatus,
    PromiseStatus,
)
from app.models.tables import (
    ContactLog,
    Customer,
    DecisionTrace,
    Invoice,
    OptOut,
    Promise,
    WebhookEvent,
)

__all__ = [
    "ContactChannel",
    "ContactLog",
    "Customer",
    "DecisionOutcome",
    "DeliveryStatus",
    "DecisionTrace",
    "EscalationState",
    "InterventionTier",
    "Invoice",
    "InvoiceStatus",
    "OptOut",
    "Promise",
    "PromiseStatus",
    "WebhookEvent",
]
