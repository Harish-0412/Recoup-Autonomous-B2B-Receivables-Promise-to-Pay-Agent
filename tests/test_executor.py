"""The executor: the step that makes a decision leave the building.

These tests are organised around the three things that can go wrong once an
agent is allowed to send real email:

* **Bypassing the gate.** An executor that can be handed a bare proposal, or an
  action the gate blocked, is a system where the safety rules are advisory.
* **Lying about what happened.** A contact row for a message that was never
  sent invalidates the caps and every recovery figure computed from them.
* **Paying for failure.** A provider outage that burns a ladder rung per
  attempt walks a whole book to final notice while sending nothing.

The gateways are substituted rather than patched -- ``FailingEmailGateway`` and
friends implement the same Protocol as the real ones, so the service under test
is the real service.
"""

from __future__ import annotations

import pytest

from app.core.domain import CaseSnapshot, CustomerSnapshot, InvoiceSnapshot
from app.core.policy import ActionType, PolicyDecision, PolicyViolation, ProposedAction
from app.models.enums import ContactChannel, DeliveryStatus, EscalationState, InvoiceStatus
from app.services.executor import (
    DryRunEmailGateway,
    DryRunPaymentGateway,
    ExecutionIntent,
    ExecutionService,
    GatewayOutcome,
    Gateways,
    templates,
)
from src.ml.versioning import utc_now

# --- fixtures ---------------------------------------------------------------


def make_case(
    *,
    invoice_id: str = "INV-9001",
    email: str | None = "ap@acme.example",
    amount: float = 250_000.0,
    days_overdue: int = 21,
) -> CaseSnapshot:
    from datetime import date, timedelta

    today = date(2026, 9, 1)
    return CaseSnapshot(
        invoice=InvoiceSnapshot(
            invoice_id=invoice_id,
            customer_id="CUS-1",
            amount=amount,
            amount_paid=0.0,
            currency="INR",
            issue_date=today - timedelta(days=days_overdue + 30),
            due_date=today - timedelta(days=days_overdue),
            payment_terms_days=30,
            days_overdue=days_overdue,
            status=InvoiceStatus.OPEN,
            escalation_state=EscalationState.MONITORING,
            ladder_index=0,
            prior_reminders_sent=0,
            days_since_last_contact=99,
            as_of=today,
        ),
        customer=CustomerSnapshot(
            customer_id="CUS-1",
            name="Acme Traders Pvt Ltd",
            email=email,
            preferred_channel=ContactChannel.EMAIL,
            tenure_months=24,
            invoice_count=18,
            avg_invoice_amount=200_000.0,
            on_time_ratio_90d=0.6,
            on_time_ratio_all_time=0.7,
            avg_days_late=9.0,
        ),
    )


def approved(
    case: CaseSnapshot,
    *,
    action_type: ActionType = ActionType.SEND_REMINDER,
    ladder_step: str = "reminder_1",
    discount_pct: float = 0.0,
) -> tuple[ProposedAction, PolicyDecision]:
    action = ProposedAction(
        invoice_id=case.invoice.invoice_id,
        action_type=action_type,
        channel=ContactChannel.EMAIL,
        ladder_step=ladder_step,
        requested_discount_pct=discount_pct,
    )
    decision = PolicyDecision(
        invoice_id=case.invoice.invoice_id,
        action_type=action_type,
        allowed=True,
        effective_discount_pct=discount_pct,
    )
    return action, decision


class FakeInvoiceRow:
    """Stands in for the ORM row the executor mutates and records against."""

    def __init__(self, business_id: str = "default") -> None:
        self.id = 1
        self.business_id = business_id
        self.payment_link_id: str | None = None
        self.payment_link_url: str | None = None
        self.prior_reminders_sent = 0
        self.last_contact_at = None
        self.status = InvoiceStatus.OPEN


class RecordingSession:
    """Captures what record_contact would have written, without a database."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj) -> None:
        self.added.append(obj)


@pytest.fixture
def session(monkeypatch):
    captured: dict = {}
    recording = RecordingSession()

    async def fake_record_contact(session_arg, invoice, business_id="default", **kwargs):
        captured.update(kwargs)
        captured["business_id"] = business_id
        from app.models.tables import ContactLog

        contact = ContactLog(invoice_pk=invoice.id, **kwargs)
        if contact.counts_as_contact:
            invoice.prior_reminders_sent += 1
            invoice.last_contact_at = utc_now()
        recording.added.append(contact)
        return contact

    from app.services import repository

    monkeypatch.setattr(repository, "record_contact", fake_record_contact)
    recording.captured = captured  # type: ignore[attr-defined]
    return recording


def dry_run_service() -> ExecutionService:
    class _S:
        DRY_RUN = True
        REPLY_ADDRESS_SECRET = "test-address-secret"
        REPLY_INBOUND_DOMAIN = "reply.recoup.test"

    return ExecutionService(
        Gateways(payments=DryRunPaymentGateway(), email=DryRunEmailGateway(), dry_run=True),
        settings=_S(),
    )


# --- the gate cannot be bypassed --------------------------------------------


def test_a_blocked_decision_cannot_become_an_intent():
    """The safety property, as a type error rather than a code review comment."""

    case = make_case()
    action, _ = approved(case)
    blocked = PolicyDecision(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.SEND_REMINDER,
        allowed=False,
        violations=[PolicyViolation(code="contact_frequency_cap", message="too soon")],
    )

    with pytest.raises(ValueError, match="did not approve"):
        ExecutionIntent.from_decision(case, action, blocked)


def test_a_non_contacting_action_cannot_become_an_intent():
    """HAND_OFF moves internal state; it must never be emailed to a customer."""

    case = make_case()
    action = ProposedAction(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.HAND_OFF,
        ladder_step="human_handoff",
    )
    decision = PolicyDecision(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.HAND_OFF,
        allowed=True,
    )

    with pytest.raises(ValueError, match="not a contacting action"):
        ExecutionIntent.from_decision(case, action, decision)


def test_a_decision_for_another_invoice_is_refused():
    """Guards against the wiring bug that emails the wrong customer."""

    case = make_case(invoice_id="INV-9001")
    action, decision = approved(case)
    wrong = decision.model_copy(update={"invoice_id": "INV-OTHER"})

    with pytest.raises(ValueError, match="INV-OTHER"):
        ExecutionIntent(invoice_id="INV-9001", case=case, action=action, decision=wrong)


def test_the_intent_quotes_the_gates_discount_not_the_request():
    """The gate may lower a discount; the customer must see the lowered one."""

    case = make_case(amount=100_000.0)
    action = ProposedAction(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.OFFER_SETTLEMENT,
        ladder_step="final_notice",
        requested_discount_pct=25.0,
    )
    decision = PolicyDecision(
        invoice_id=case.invoice.invoice_id,
        action_type=ActionType.OFFER_SETTLEMENT,
        allowed=True,
        effective_discount_pct=10.0,
    )
    intent = ExecutionIntent.from_decision(case, action, decision)

    assert intent.discount_pct == 10.0
    assert intent.payable_amount == pytest.approx(90_000.0)


# --- the happy path ---------------------------------------------------------


@pytest.mark.anyio
async def test_a_dry_run_renders_everything_and_delivers_nothing(session):
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    service = dry_run_service()
    invoice = FakeInvoiceRow()

    result = await service.execute(session, intent, invoice)

    assert result.status is DeliveryStatus.SIMULATED
    assert result.delivered is True
    assert result.payment_link_id
    assert result.payment_link_url
    assert result.error is None
    # Fully rendered, not a stub: the point of a dry run is to read the message.
    assert "Acme Traders" in result.body_preview
    assert result.subject


@pytest.mark.anyio
async def test_a_delivered_message_consumes_a_contact_slot(session):
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    invoice = FakeInvoiceRow()

    await dry_run_service().execute(session, intent, invoice)

    assert invoice.prior_reminders_sent == 1
    assert invoice.last_contact_at is not None


@pytest.mark.anyio
async def test_the_payment_link_is_stored_for_webhook_reconciliation(session):
    """get_invoice_by_payment_link is how a payment finds its invoice."""

    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    invoice = FakeInvoiceRow()

    result = await dry_run_service().execute(session, intent, invoice)

    assert invoice.payment_link_id == result.payment_link_id
    assert invoice.payment_link_url == result.payment_link_url


@pytest.mark.anyio
async def test_provider_ids_reach_the_contact_row(session):
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))

    await dry_run_service().execute(session, intent, FakeInvoiceRow())

    written = session.captured
    assert written["status"] is DeliveryStatus.SIMULATED
    assert written["provider_message_id"]
    assert written["payment_link_id"]
    assert written["provider_error"] is None


# --- failure must not cost anything ----------------------------------------


class FailingEmailGateway:
    async def send_email(self, *, to, subject, html, text, reply_to=None):
        return GatewayOutcome.failed("resend.emails.send failed: 503 upstream")


class FailingPaymentGateway:
    async def create_payment_link(self, **kwargs):
        return GatewayOutcome.failed("razorpay.payment_link.create timed out after 20s")

    async def fetch_payment_link(self, link_id):
        return GatewayOutcome.failed("not found")


def service_with(payments=None, email=None) -> ExecutionService:
    class _S:
        DRY_RUN = False
        REPLY_ADDRESS_SECRET = "test-address-secret"
        REPLY_INBOUND_DOMAIN = "reply.recoup.test"

    return ExecutionService(
        Gateways(
            payments=payments or DryRunPaymentGateway(),
            email=email or DryRunEmailGateway(),
            dry_run=False,
        ),
        settings=_S(),
    )


@pytest.mark.anyio
async def test_a_failed_send_does_not_consume_a_contact_slot(session):
    """The rule that stops an outage from exhausting the whole book."""

    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    invoice = FakeInvoiceRow()

    result = await service_with(email=FailingEmailGateway()).execute(session, intent, invoice)

    assert result.status is DeliveryStatus.FAILED
    assert result.delivered is False
    assert invoice.prior_reminders_sent == 0
    assert invoice.last_contact_at is None


@pytest.mark.anyio
async def test_a_failed_send_is_still_recorded(session):
    """Failures are visible, not swallowed -- they are just not contact."""

    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))

    await service_with(email=FailingEmailGateway()).execute(session, intent, FakeInvoiceRow())

    assert session.captured["status"] is DeliveryStatus.FAILED
    assert "503" in session.captured["provider_error"]


@pytest.mark.anyio
async def test_a_created_link_survives_a_failed_send(session):
    """The link is live; a customer could still pay it, so it must reconcile."""

    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    invoice = FakeInvoiceRow()

    result = await service_with(email=FailingEmailGateway()).execute(session, intent, invoice)

    assert result.payment_link_id
    assert invoice.payment_link_id == result.payment_link_id


@pytest.mark.anyio
async def test_no_email_is_sent_when_the_payment_link_fails(session):
    """A reminder with no way to pay is worse than no reminder."""

    email = DryRunEmailGateway()
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))

    result = await service_with(payments=FailingPaymentGateway(), email=email).execute(
        session, intent, FakeInvoiceRow()
    )

    assert result.status is DeliveryStatus.FAILED
    assert email.sent == []


@pytest.mark.anyio
async def test_a_customer_with_no_email_fails_cleanly(session):
    case = make_case(email=None)
    intent = ExecutionIntent.from_decision(case, *approved(case))

    result = await dry_run_service().execute(session, intent, FakeInvoiceRow())

    assert result.status is DeliveryStatus.FAILED
    assert "no email" in (result.error or "")


@pytest.mark.anyio
async def test_the_executor_never_raises_on_provider_failure(session):
    """One bad provider must not end a batch."""

    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))

    # Must return, not raise.
    result = await service_with(
        payments=FailingPaymentGateway(), email=FailingEmailGateway()
    ).execute(session, intent, FakeInvoiceRow())

    assert result.status is DeliveryStatus.FAILED


# --- link reuse -------------------------------------------------------------


class ReusableLinkGateway(DryRunPaymentGateway):
    def __init__(self, status: str = "created") -> None:
        super().__init__()
        self._status = status

    async def fetch_payment_link(self, link_id):
        return GatewayOutcome.success({"id": link_id, "status": self._status})


@pytest.mark.anyio
async def test_a_live_link_is_reused_not_reminted(session):
    """Several live links for one invoice is a reconciliation problem."""

    payments = ReusableLinkGateway(status="created")
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    invoice = FakeInvoiceRow()
    invoice.payment_link_id = "plink_existing"
    invoice.payment_link_url = "https://rzp.io/i/existing"

    result = await service_with(payments=payments).execute(session, intent, invoice)

    assert result.payment_link_reused is True
    assert result.payment_link_id == "plink_existing"
    assert payments.created == []


@pytest.mark.anyio
async def test_a_paid_link_is_replaced(session):
    """A settled link cannot collect the next reminder's money."""

    payments = ReusableLinkGateway(status="paid")
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))
    invoice = FakeInvoiceRow()
    invoice.payment_link_id = "plink_paid"
    invoice.payment_link_url = "https://rzp.io/i/paid"

    result = await service_with(payments=payments).execute(session, intent, invoice)

    assert result.payment_link_reused is False
    assert result.payment_link_id != "plink_paid"
    assert len(payments.created) == 1


# --- copy -------------------------------------------------------------------


def test_every_rung_renders_and_carries_a_link():
    case = make_case()
    link = None
    from app.services.executor.contract import PaymentLink

    link = PaymentLink(link_id="plink_1", url="https://rzp.io/i/x", amount=1.0)

    for step in ("reminder_1", "reminder_2", "final_notice"):
        action, decision = approved(case, ladder_step=step)
        message = templates.render(ExecutionIntent.from_decision(case, action, decision), link)

        assert case.invoice.invoice_id in message.text
        assert link.url in message.text
        assert link.url in message.html
        assert "unsubscribe" in message.text.lower()


def test_the_settlement_message_quotes_the_discounted_amount():
    case = make_case(amount=100_000.0)
    action, decision = approved(case, action_type=ActionType.OFFER_SETTLEMENT, discount_pct=10.0)
    intent = ExecutionIntent.from_decision(case, action, decision)
    from app.services.executor.contract import PaymentLink

    message = templates.render(
        intent, PaymentLink(link_id="p", url="https://rzp.io/i/x", amount=90_000.0)
    )

    assert "90,000" in message.text
    assert "10%" in message.text


def test_indian_digit_grouping():
    assert templates.format_inr(180_000) == "Rs 1,80,000"
    assert templates.format_inr(1_000) == "Rs 1,000"
    assert templates.format_inr(500) == "Rs 500"
    assert templates.format_inr(12_345_678) == "Rs 1,23,45,678"


# --- the payment-link cap ---------------------------------------------------
#
# Razorpay refuses a payment link above Rs 5,00,000. Found by asking the real
# test-mode API, not from the docs: Rs 5,00,000 is accepted, Rs 5,20,000 comes
# back "amount exceeds maximum amount allowed".


@pytest.mark.anyio
async def test_an_invoice_over_the_cap_is_still_contacted(session):
    """The bug this guards: refusing to write to a customer because their
    invoice is *too large* silently exempts the biggest debts in the book."""

    payments = DryRunPaymentGateway()
    case = make_case(amount=520_000.0)
    intent = ExecutionIntent.from_decision(case, *approved(case))

    result = await service_with(payments=payments).execute(session, intent, FakeInvoiceRow())

    assert result.delivered is True
    assert result.payment_link_id is None
    # No link was even attempted: this will never succeed on retry, so asking
    # is pure latency and a guaranteed provider error in the logs.
    assert payments.created == []


@pytest.mark.anyio
async def test_an_invoice_at_the_cap_still_gets_a_link(session):
    payments = DryRunPaymentGateway()
    case = make_case(amount=500_000.0)
    intent = ExecutionIntent.from_decision(case, *approved(case))

    result = await service_with(payments=payments).execute(session, intent, FakeInvoiceRow())

    assert result.delivered is True
    assert result.payment_link_id is not None
    assert len(payments.created) == 1


@pytest.mark.anyio
async def test_a_discount_can_bring_an_invoice_under_the_cap(session):
    """The cap applies to what is actually being asked for, not the face value."""

    payments = DryRunPaymentGateway()
    case = make_case(amount=520_000.0)
    action, decision = approved(case, action_type=ActionType.OFFER_SETTLEMENT, discount_pct=10.0)
    intent = ExecutionIntent.from_decision(case, action, decision)
    assert intent.payable_amount == pytest.approx(468_000.0)

    result = await service_with(payments=payments).execute(session, intent, FakeInvoiceRow())

    assert result.payment_link_id is not None


def test_a_message_without_a_link_does_not_trail_off():
    """Every body used to end by introducing the link, so a message with none
    stopped mid-sentence on a colon."""

    case = make_case(amount=520_000.0)
    intent = ExecutionIntent.from_decision(case, *approved(case))

    message = templates.render(intent, None)

    assert not message.text.rstrip().endswith(":")
    assert "bank transfer details" in message.text
    assert "settle it here:" not in message.text


def test_a_message_with_a_link_offers_it():
    from app.services.executor.contract import PaymentLink

    case = make_case(amount=100_000.0)
    intent = ExecutionIntent.from_decision(case, *approved(case))

    message = templates.render(
        intent, PaymentLink(link_id="p", url="https://rzp.io/i/x", amount=100_000.0)
    )

    assert "You can settle it here:" in message.text
    assert "bank transfer details" not in message.text


# --- reply routing ----------------------------------------------------------


@pytest.mark.anyio
async def test_every_message_carries_a_tagged_reply_to(session):
    """Without it an inbound reply cannot be matched to its invoice."""

    email = DryRunEmailGateway()
    case = make_case()
    intent = ExecutionIntent.from_decision(case, *approved(case))

    await service_with(email=email).execute(session, intent, FakeInvoiceRow())

    reply_to = email.sent[-1]["reply_to"]
    assert reply_to.startswith(f"reply+default.{case.invoice.invoice_id}.")

    from app.services.reply_routing import resolve_invoice_id, resolve_tenant_invoice

    assert resolve_invoice_id(reply_to, secret="test-address-secret") == case.invoice.invoice_id
    assert resolve_tenant_invoice(reply_to, secret="test-address-secret") == (
        "default",
        case.invoice.invoice_id,
    )
