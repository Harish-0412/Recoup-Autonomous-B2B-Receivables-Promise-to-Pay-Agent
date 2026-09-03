"""Async adapters over the two synchronous provider SDKs.

Three jobs, all of which the service layer would otherwise have to do inline:

**Keep blocking I/O off the event loop.** Both ``razorpay`` and ``resend`` are
synchronous ``requests``-based clients. Called directly from an async endpoint
they block the loop for the whole round trip, so one slow provider stalls every
other request in the process. Every call here goes through
``anyio.to_thread.run_sync``.

**Bound the wait.** A hung provider connection with no timeout blocks a worker
thread indefinitely, and thread pools are finite. Each call is wrapped in
``anyio.fail_after``.

**Turn failures into values.** Providers fail; batches must not. Each method
returns a ``GatewayOutcome`` rather than raising, so the caller reasons about a
result instead of arranging exception handling at every call site.

The ``Protocol`` definitions are what make the dry run and the tests possible
without patching module globals: ``DryRunGateways`` is a peer implementation,
not a monkeypatch, and the service cannot tell the difference.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import anyio

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Providers are given this long before the attempt is abandoned. Generous
#: enough for a slow round trip, short enough that a stuck call cannot hold a
#: worker thread for the life of the process.
DEFAULT_TIMEOUT_SECONDS = 20.0

#: Razorpay refuses a payment link above this, with "amount exceeds maximum
#: amount allowed". Found by asking it: Rs 5,00,000 is accepted and Rs 5,20,000
#: is not.
#:
#: Checked before the call rather than by matching that error string, because a
#: known limit should not be discovered through a failed request -- and because
#: this is not a transient failure to retry. B2B invoices routinely exceed it,
#: and an invoice over the cap must still get its reminder; see
#: ``ExecutionService.execute``.
RAZORPAY_PAYMENT_LINK_MAX_INR = 500_000.0


@dataclass(frozen=True)
class GatewayOutcome:
    """Success with a payload, or failure with a reason. Never both."""

    ok: bool
    payload: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def success(cls, payload: dict[str, Any]) -> GatewayOutcome:
        return cls(ok=True, payload=payload)

    @classmethod
    def failed(cls, error: str) -> GatewayOutcome:
        return cls(ok=False, error=error)

    def require(self) -> dict[str, Any]:
        if not self.ok or self.payload is None:
            raise RuntimeError(self.error or "gateway call failed")
        return self.payload


@runtime_checkable
class PaymentGateway(Protocol):
    """Creates payment links and reports on them."""

    async def create_payment_link(
        self,
        *,
        amount: float,
        currency: str,
        description: str,
        customer_name: str,
        customer_email: str,
        notes: dict[str, str],
    ) -> GatewayOutcome: ...

    async def fetch_payment_link(self, link_id: str) -> GatewayOutcome: ...


@runtime_checkable
class EmailGateway(Protocol):
    """Delivers one email."""

    async def send_email(
        self, *, to: str, subject: str, html: str, text: str, reply_to: str | None = None
    ) -> GatewayOutcome: ...


async def _call(
    label: str,
    fn: Callable[..., Any],
    *args: Any,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> GatewayOutcome:
    """Run a blocking provider call in a worker thread, with a deadline.

    ``functools.partial`` rather than ``run_sync(fn, *args)`` because
    ``to_thread.run_sync`` takes positional arguments only, and both SDKs are
    called with keywords.
    """

    from functools import partial

    try:
        with anyio.fail_after(timeout):
            result = await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))
    except TimeoutError:
        logger.warning("Provider call timed out", provider_call=label, timeout=timeout)
        return GatewayOutcome.failed(f"{label} timed out after {timeout:g}s")
    except Exception as exc:
        logger.warning("Provider call failed", provider_call=label, error=str(exc))
        return GatewayOutcome.failed(f"{label} failed: {type(exc).__name__}: {exc}")

    return GatewayOutcome.success(dict(result) if result else {})


class RazorpayGateway:
    """Live Razorpay payment links."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        # Built lazily so importing this module does not require credentials --
        # which matters because the dry-run path imports it too.
        if self._client is None:
            from app.services.razorpay_client import get_razorpay_client

            self._client = get_razorpay_client()
        return self._client

    async def create_payment_link(
        self,
        *,
        amount: float,
        currency: str,
        description: str,
        customer_name: str,
        customer_email: str,
        notes: dict[str, str],
    ) -> GatewayOutcome:
        # Razorpay works in paise. Rounding here rather than truncating avoids
        # a link that asks for one paisa less than the invoice.
        return await _call(
            "razorpay.payment_link.create",
            self.client.create_payment_link,
            amount=int(round(amount * 100)),
            currency=currency,
            description=description,
            customer_name=customer_name,
            customer_email=customer_email,
            # The agent sends its own message; letting Razorpay also notify
            # would double-contact the customer and bypass the contact caps.
            notify_email=False,
            notify_sms=False,
            notes=notes,
        )

    async def fetch_payment_link(self, link_id: str) -> GatewayOutcome:
        return await _call("razorpay.payment_link.fetch", self.client.fetch_payment_link, link_id)


class ResendGateway:
    """Live Resend email delivery."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from app.services.resend_client import get_resend_client

            self._client = get_resend_client()
        return self._client

    async def send_email(
        self, *, to: str, subject: str, html: str, text: str, reply_to: str | None = None
    ) -> GatewayOutcome:
        return await _call(
            "resend.emails.send",
            self.client.send_email,
            to=to,
            subject=subject,
            html=html,
            text=text,
            reply_to=reply_to,
        )


class DryRunPaymentGateway:
    """Mints deterministic fake links and never calls Razorpay."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_payment_link(
        self,
        *,
        amount: float,
        currency: str,
        description: str,
        customer_name: str,
        customer_email: str,
        notes: dict[str, str],
    ) -> GatewayOutcome:
        invoice_id = notes.get("invoice_id", "unknown")
        # Deterministic from the invoice, so a dry run is reproducible and two
        # runs of the same seed produce comparable output.
        link_id = f"plink_dryrun_{invoice_id.replace('-', '').lower()}"
        record = {
            "id": link_id,
            "short_url": f"https://rzp.io/i/dryrun/{invoice_id}",
            "amount": int(round(amount * 100)),
            "currency": currency,
            "description": description,
            "status": "created",
        }
        self.created.append(record)
        logger.info(
            "DRY RUN: payment link not created",
            invoice_id=invoice_id,
            amount=amount,
            link_id=link_id,
        )
        return GatewayOutcome.success(record)

    async def fetch_payment_link(self, link_id: str) -> GatewayOutcome:
        return GatewayOutcome.success({"id": link_id, "status": "created"})


class DryRunEmailGateway:
    """Logs the fully rendered message instead of sending it.

    The whole message is logged, not a summary: the point of a dry run is to
    read exactly what the customer would have received.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_email(
        self, *, to: str, subject: str, html: str, text: str, reply_to: str | None = None
    ) -> GatewayOutcome:
        record = {"id": f"email_dryrun_{len(self.sent) + 1}", "to": to, "subject": subject}
        self.sent.append({**record, "text": text, "html": html, "reply_to": reply_to})
        logger.info(
            "DRY RUN: email not sent",
            to=to,
            subject=subject,
            reply_to=reply_to,
            body=text,
        )
        return GatewayOutcome.success(record)


@dataclass(frozen=True)
class Gateways:
    """The pair the executor needs, chosen together."""

    payments: PaymentGateway
    email: EmailGateway
    dry_run: bool

    @classmethod
    def for_settings(cls, settings: Any) -> Gateways:
        """Live gateways, or dry-run ones when ``DRY_RUN`` is set.

        Chosen once here rather than branched on at each call site, so there is
        exactly one place where "are we really sending?" is decided.
        """

        if settings.DRY_RUN:
            logger.info("Executor running in DRY_RUN mode; nothing will be delivered")
            return cls(payments=DryRunPaymentGateway(), email=DryRunEmailGateway(), dry_run=True)
        return cls(payments=RazorpayGateway(), email=ResendGateway(), dry_run=False)
