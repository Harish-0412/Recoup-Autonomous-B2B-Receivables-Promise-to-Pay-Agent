"""ORM models and the enums shared across the persistence, core and API layers.

Import from here rather than from ``app.models.tables`` so call sites stay
stable if a table moves. Importing this package also registers every model on
``Base.metadata``, which is what makes Alembic's autogenerate see
them -- ``app.db.session`` deliberately does not import models itself, to avoid
a circular import.
"""

from app.models.enums import (
    AllocationSource,
    ContactChannel,
    DecisionOutcome,
    DeliveryStatus,
    EscalationState,
    IntegrationProvider,
    InterventionTier,
    InvoiceStatus,
    PromiseStatus,
    ReplyDisposition,
)
from app.models.tables import (
    DEFAULT_BUSINESS_ID,
    BatchRunRecord,
    Business,
    ContactLog,
    Customer,
    CustomerDriftFlag,
    DecisionTrace,
    InboundReply,
    IntegrationCredential,
    Invoice,
    OptOut,
    PaymentAllocation,
    Promise,
    RecoupEventRecord,
    WebhookEvent,
)

__all__ = [
    "DEFAULT_BUSINESS_ID",
    "AllocationSource",
    "BatchRunRecord",
    "Business",
    "ContactChannel",
    "ContactLog",
    "Customer",
    "CustomerDriftFlag",
    "DecisionOutcome",
    "DeliveryStatus",
    "DecisionTrace",
    "EscalationState",
    "IntegrationCredential",
    "IntegrationProvider",
    "InterventionTier",
    "InboundReply",
    "Invoice",
    "InvoiceStatus",
    "OptOut",
    "PaymentAllocation",
    "Promise",
    "PromiseStatus",
    "RecoupEventRecord",
    "ReplyDisposition",
    "WebhookEvent",
]
