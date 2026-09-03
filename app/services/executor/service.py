"""The executor: turns an approved decision into a message that actually goes.

Before this existed, ``trigger_cycle`` decided correctly, gated correctly, and
then called ``record_contact`` for a message nobody had sent. The contact caps,
the frequency gate and every recovery figure were computed from rows describing
events that never happened.

**The ordering is the correctness property.** Send first, persist second:

    reuse-or-create payment link -> render -> send -> record outcome

Persisting first would mean a crash between the write and the send leaves a
database that claims contact was made. Sending first means the opposite failure
-- a delivered message with no row -- which is recoverable, because the next
cycle's cap check is merely conservative rather than wrong. Given a choice
between over-counting contact and under-counting it, under-counting is the one
that cannot harass a customer.

**A failed send must not cost anything.** On failure the executor writes a
``FAILED`` contact row and returns; the caller leaves ``ladder_index`` alone,
so the next cycle retries the same rung. A provider outage that burned a rung
per attempt would walk an entire book to final notice without sending anything.

**Links are reused, not reminted.** Creating a fresh payment link every cycle
leaves the customer holding several live links for one invoice, any of which
could be paid -- and only one of which the webhook would recognise. The
executor reuses the invoice's existing link while it is still payable.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import DeliveryStatus
from app.models.tables import Invoice
from app.services import repository
from app.services.executor import templates
from app.services.executor.contract import (
    ExecutionIntent,
    ExecutionResult,
    PaymentLink,
    RenderedMessage,
)
from app.services.executor.gateways import Gateways

logger = get_logger(__name__)

#: Link states that still accept payment. Anything else needs a fresh link.
_PAYABLE_LINK_STATES = frozenset({"created", "partially_paid"})


class ExecutionService:
    """Delivers approved actions. One instance per batch, not per invoice."""

    def __init__(self, gateways: Gateways, *, settings=None) -> None:
        self.gateways = gateways
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self.settings = settings

    # -- payment link ------------------------------------------------------

    async def _resolve_payment_link(
        self, intent: ExecutionIntent, invoice: Invoice
    ) -> tuple[PaymentLink | None, str | None]:
        """Reuse the invoice's live link, or create one. Returns (link, error)."""

        if invoice.payment_link_id and invoice.payment_link_url:
            existing = await self.gateways.payments.fetch_payment_link(invoice.payment_link_id)
            # A fetch failure is not fatal: we would rather mint a new link than
            # abandon the send because Razorpay could not describe the old one.
            if existing.ok and (existing.payload or {}).get("status") in _PAYABLE_LINK_STATES:
                return (
                    PaymentLink(
                        link_id=invoice.payment_link_id,
                        url=invoice.payment_link_url,
                        amount=intent.payable_amount,
                        reused=True,
                    ),
                    None,
                )

        created = await self.gateways.payments.create_payment_link(
            amount=intent.payable_amount,
            currency=intent.case.invoice.currency,
            description=templates.payment_link_description(intent),
            customer_name=intent.case.customer.name,
            customer_email=intent.recipient or "",
            # Carried on the link so the webhook can find its way home even if
            # the local payment_link_id were ever lost.
            notes={
                "invoice_id": intent.invoice_id,
                "customer_id": intent.case.customer.customer_id,
                "ladder_step": intent.ladder_step,
            },
        )
        if not created.ok:
            return None, created.error or "payment link creation failed"

        payload = created.payload or {}
        link_id = str(payload.get("id") or "")
        url = str(payload.get("short_url") or payload.get("url") or "")
        if not link_id or not url:
            return None, f"payment link response missing id/url: {payload!r}"

        return (
            PaymentLink(link_id=link_id, url=url, amount=intent.payable_amount),
            None,
        )

    # -- persistence -------------------------------------------------------

    async def _record(
        self,
        session: AsyncSession,
        invoice: Invoice,
        intent: ExecutionIntent,
        result: ExecutionResult,
        message: RenderedMessage | None,
    ) -> None:
        """Write the attempt, and only touch counters when it was delivered."""

        await repository.record_contact(
            session,
            invoice,
            channel=intent.channel,
            ladder_step=intent.ladder_step,
            subject=result.subject or (message.subject if message else ""),
            body_preview=result.body_preview,
            status=result.status,
            provider_message_id=result.provider_message_id,
            payment_link_id=result.payment_link_id,
            provider_error=result.error,
        )

        # A link that was created must be stored even if the *email* then
        # failed: it is live, the customer may yet be given it, and the webhook
        # has to be able to reconcile a payment against it.
        if result.payment_link_id and result.payment_link_url:
            invoice.payment_link_id = result.payment_link_id
            invoice.payment_link_url = result.payment_link_url

    # -- the main path -----------------------------------------------------

    async def execute(
        self,
        session: AsyncSession,
        intent: ExecutionIntent,
        invoice: Invoice,
    ) -> ExecutionResult:
        """Deliver one approved action. Never raises on a provider failure."""

        log = logger.bind(
            invoice_id=intent.invoice_id,
            ladder_step=intent.ladder_step,
            dry_run=self.gateways.dry_run,
        )

        recipient = intent.recipient
        if not recipient:
            # Not a provider failure and not retryable by waiting: this customer
            # has no address. Recorded so it shows up as a data problem.
            result = ExecutionResult.failure(intent, "customer has no email address on file")
            await self._record(session, invoice, intent, result, None)
            log.warning("Cannot contact customer", reason="no_email")
            return result

        link, link_error = await self._resolve_payment_link(intent, invoice)
        if link is None:
            message = templates.render(intent, None)
            result = ExecutionResult.failure(
                intent,
                link_error or "payment link unavailable",
                subject=message.subject,
                body_preview=message.preview(),
            )
            await self._record(session, invoice, intent, result, message)
            log.warning("Payment link failed; nothing sent", error=link_error)
            return result

        message = templates.render(intent, link)

        sent = await self.gateways.email.send_email(
            to=recipient,
            subject=message.subject,
            html=message.html,
            text=message.text,
        )

        if not sent.ok:
            result = ExecutionResult.failure(
                intent,
                sent.error or "email send failed",
                subject=message.subject,
                body_preview=message.preview(),
                payment_link_id=link.link_id,
            )
            # The link is real and must still be stored, so a customer who pays
            # it any other way is reconciled correctly.
            result = result.model_copy(
                update={"payment_link_url": link.url, "payment_link_reused": link.reused}
            )
            await self._record(session, invoice, intent, result, message)
            log.warning("Send failed; ladder not advanced", error=sent.error)
            return result

        payload = sent.payload or {}
        result = ExecutionResult(
            invoice_id=intent.invoice_id,
            status=(DeliveryStatus.SIMULATED if self.gateways.dry_run else DeliveryStatus.SENT),
            channel=intent.channel,
            ladder_step=intent.ladder_step,
            subject=message.subject,
            body_preview=message.preview(),
            provider_message_id=str(payload.get("id") or "") or None,
            payment_link_id=link.link_id,
            payment_link_url=link.url,
            payment_link_reused=link.reused,
            amount_requested=intent.payable_amount,
        )
        await self._record(session, invoice, intent, result, message)
        log.info(
            "Delivered",
            status=result.status.value,
            provider_message_id=result.provider_message_id,
            payment_link_id=link.link_id,
            payment_link_reused=link.reused,
            amount=result.amount_requested,
        )
        return result


def build_execution_service(settings=None) -> ExecutionService:
    """The executor the app uses, wired from configuration."""

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    return ExecutionService(Gateways.for_settings(settings), settings=settings)
