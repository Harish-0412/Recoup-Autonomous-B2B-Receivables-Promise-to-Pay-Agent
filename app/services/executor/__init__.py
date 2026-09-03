"""Execution: the step that makes the agent's decisions leave the building.

    score -> propose -> gate -> transition -> **execute**

Four modules, split so each seam can be tested without the ones beside it:

* ``contract``  -- ``ExecutionIntent``, constructible only from an *approved*
                   ``PolicyDecision``, so the gate cannot be bypassed by
                   calling the executor directly.
* ``templates`` -- pure message rendering per ladder rung. No I/O, so a dry run
                   produces byte-identical copy to a live send.
* ``gateways``  -- async adapters over the synchronous Razorpay and Resend
                   SDKs, with timeouts, thread offloading, and dry-run peers.
* ``service``   -- the send-then-persist ordering, and the rule that a failed
                   send costs neither a contact slot nor a ladder rung.
"""

from app.services.executor.contract import (
    ExecutionIntent,
    ExecutionResult,
    PaymentLink,
    RenderedMessage,
)
from app.services.executor.gateways import (
    DryRunEmailGateway,
    DryRunPaymentGateway,
    EmailGateway,
    GatewayOutcome,
    Gateways,
    PaymentGateway,
    RazorpayGateway,
    ResendGateway,
)
from app.services.executor.service import ExecutionService, build_execution_service

__all__ = [
    "DryRunEmailGateway",
    "DryRunPaymentGateway",
    "EmailGateway",
    "ExecutionIntent",
    "ExecutionResult",
    "ExecutionService",
    "GatewayOutcome",
    "Gateways",
    "PaymentGateway",
    "PaymentLink",
    "RazorpayGateway",
    "RenderedMessage",
    "ResendGateway",
    "build_execution_service",
]
