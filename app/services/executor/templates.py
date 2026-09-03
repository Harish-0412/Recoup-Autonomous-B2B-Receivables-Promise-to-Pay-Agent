"""Message copy, rendered per ladder rung.

Pure functions over an :class:`~app.services.executor.contract.ExecutionIntent`:
no I/O, no clock, no provider. That makes the copy testable on its own, and it
means the dry run renders byte-identical output to a live send.

**The rungs differ in firmness, not in politeness.** This is a B2B receivables
agent writing to another business's accounts-payable desk, and the escalation
that works there is specificity -- naming the invoice, the age, the amount, and
the next step -- not indignation. Every rung stays civil, because the customer
relationship usually survives the collection, and because India's debt
collection guidance treats harassment as a compliance matter rather than a
style one.

Two rules hold across every template:

* **Never invent a fact.** Amounts, dates and discounts come from the intent,
  which got them from the gate. A template may not compute a new number.
* **Always give a way to pay.** Every message carries the payment link. A
  reminder that makes paying harder than ignoring it is not a reminder.
"""

from __future__ import annotations

from app.core.policy import ActionType
from app.services.executor.contract import ExecutionIntent, PaymentLink, RenderedMessage


def format_inr(amount: float) -> str:
    """Format rupees with Indian digit grouping: 180000 -> '1,80,000'."""

    whole = int(round(amount))
    text = str(abs(whole))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        text = ",".join([*parts, tail])
    return f"{'-' if whole < 0 else ''}Rs {text}"


def _greeting(intent: ExecutionIntent) -> str:
    name = intent.case.customer.name.strip()
    return f"Hello {name}," if name else "Hello,"


def _subject(intent: ExecutionIntent) -> str:
    invoice = intent.case.invoice
    days = invoice.days_overdue
    amount = format_inr(intent.outstanding)

    if intent.action_type is ActionType.OFFER_SETTLEMENT or intent.discount_pct > 0:
        return f"Settlement offer on invoice {invoice.invoice_id} - {amount} outstanding"
    if intent.ladder_step == "final_notice":
        return f"Final notice: invoice {invoice.invoice_id}, {days} days overdue"
    if intent.action_type is ActionType.ESCALATE:
        return f"Overdue {days} days: invoice {invoice.invoice_id} ({amount})"
    return f"Payment reminder: invoice {invoice.invoice_id} ({amount})"


#: Body copy per rung. Firmness rises; the tone stays professional throughout.
_BODIES: dict[str, str] = {
    "reminder_1": (
        "Our records show invoice {invoice_id} for {amount} became due on "
        "{due_date} and is now {days} days past due.\n\n"
        "If it is already scheduled for payment, please ignore this note. "
        "Otherwise you can settle it here:"
    ),
    "reminder_2": (
        "We wrote to you about invoice {invoice_id} for {amount}, which was due "
        "on {due_date} and is now {days} days past due. We have not yet seen "
        "the payment.\n\n"
        "If something is holding it up -- a missing purchase order, a query on "
        "the amount -- please reply and tell us; we would rather resolve it "
        "than keep writing. To settle it now:"
    ),
    "final_notice": (
        "Invoice {invoice_id} for {amount} is now {days} days past due, and our "
        "previous reminders have gone unanswered.\n\n"
        "Unless the amount is paid or you contact us to arrange terms, this "
        "account will be passed to our team for manual recovery. We would "
        "prefer to close it here:"
    ),
}

_SETTLEMENT_BODY = (
    "Invoice {invoice_id} for {amount} is {days} days past due.\n\n"
    "To close this without further escalation, we can accept "
    "{settlement_amount} -- a {discount_pct:g}% reduction -- if it is paid "
    "against the link below. This offer applies to this invoice only:"
)

_SIGN_OFF = (
    "If you believe this invoice is incorrect, reply to this email and we will "
    "put collection on hold while we look into it.\n\n"
    "Recoup, on behalf of your supplier's accounts receivable team."
)

#: A reply that opts out. Named in every message because an opt-out route that
#: nobody is told about is not really an opt-out route.
_OPT_OUT = 'To stop receiving reminders about this account, reply with "unsubscribe".'


def _body_template(intent: ExecutionIntent) -> str:
    if intent.discount_pct > 0 or intent.action_type is ActionType.OFFER_SETTLEMENT:
        return _SETTLEMENT_BODY
    return _BODIES.get(intent.ladder_step, _BODIES["reminder_1"])


def render(intent: ExecutionIntent, link: PaymentLink | None) -> RenderedMessage:
    """Compose the message for this intent.

    ``link`` is optional only so that a payment-link failure can still produce
    a message for the failed-attempt record. A delivered message always has one.
    """

    invoice = intent.case.invoice
    body = _body_template(intent).format(
        invoice_id=invoice.invoice_id,
        amount=format_inr(intent.outstanding),
        due_date=invoice.due_date.strftime("%d %b %Y"),
        days=invoice.days_overdue,
        settlement_amount=format_inr(intent.payable_amount),
        discount_pct=intent.discount_pct,
    )

    subject = _subject(intent)
    pay_url = link.url if link is not None else ""
    pay_label = f"Pay {format_inr(intent.payable_amount)}"

    text_parts = [_greeting(intent), "", body, ""]
    if pay_url:
        text_parts += [f"{pay_label}: {pay_url}", ""]
    text_parts += [_SIGN_OFF, "", _OPT_OUT]
    text = "\n".join(text_parts)

    button = (
        f'<p style="margin:28px 0"><a href="{pay_url}" '
        'style="background:#0E6B62;color:#fff;padding:12px 22px;border-radius:4px;'
        'text-decoration:none;display:inline-block;font-weight:600">'
        f"{pay_label}</a></p>"
        if pay_url
        else ""
    )
    paragraphs = "".join(
        f'<p style="margin:0 0 14px">{part}</p>' for part in body.split("\n\n") if part.strip()
    )
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;line-height:1.6;color:#12202B;max-width:560px">'
        f'<p style="margin:0 0 14px">{_greeting(intent)}</p>'
        f"{paragraphs}"
        f"{button}"
        f'<p style="margin:24px 0 0;font-size:13px;color:#4A5C62">{_SIGN_OFF}</p>'
        f'<p style="margin:14px 0 0;font-size:12px;color:#7C8D91">{_OPT_OUT}</p>'
        "</div>"
    )

    return RenderedMessage(subject=subject, html=html, text=text)


def payment_link_description(intent: ExecutionIntent) -> str:
    """The description shown on the Razorpay checkout page."""

    return f"Invoice {intent.case.invoice.invoice_id}"
