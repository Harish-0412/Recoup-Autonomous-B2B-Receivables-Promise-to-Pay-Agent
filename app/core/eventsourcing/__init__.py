"""Event-sourcing domain and application package for Recoup.

Powered by ``eventsourcing`` (pyeventsourcing).
"""

from __future__ import annotations

from app.core.eventsourcing.application import (
    RecoupEventApp,
    invoice_id_to_uuid,
)
from app.core.eventsourcing.domain import InvoiceCaseAggregate

_default_app: RecoupEventApp | None = None


def get_event_app() -> RecoupEventApp:
    """Return the process-wide default RecoupEventApp instance."""
    global _default_app
    if _default_app is None:
        _default_app = RecoupEventApp()
    return _default_app


def reset_event_app() -> RecoupEventApp:
    """Reset the default event app (useful for isolated tests)."""
    global _default_app
    _default_app = RecoupEventApp()
    return _default_app


__all__ = [
    "InvoiceCaseAggregate",
    "RecoupEventApp",
    "get_event_app",
    "invoice_id_to_uuid",
    "reset_event_app",
]
