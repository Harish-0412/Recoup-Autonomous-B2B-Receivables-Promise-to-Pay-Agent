"""Stage B: the label-first labelled corpus for the reply classifier.

Text is generated **from** a known label, never labelled after the fact, so the
ground truth is correct by construction rather than by annotation. Every
example carries its intent, its entity values and the template it came from.

Two things here are worth knowing before you read a metric off this dataset:

**Splitting is grouped by template, not random.** With templated text, a random
split puts near-identical sentences in both train and test, and the classifier
scores in the high nineties by memorising templates. Grouping by template so no
template appears in two splits gives a lower, honest number: it measures
generalisation to phrasing the model has not seen. ``random`` is available for
comparison, and the training report prints both.

**These are templates with surface variation, not LLM paraphrases.** The upside
is that generation is free, offline, reproducible from a seed, and runnable in
CI. The cost is less lexical diversity than an LLM would produce. LLM
paraphrase augmentation slots in on top of this without changing the schema.
"""

import re
from collections.abc import Sequence
from datetime import date, timedelta
from enum import Enum
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from src.data.synthetic_generator import (
    draw_amount,
    make_rng,
    render_amount,
    render_date_phrase,
)
from src.ml.schemas import IntentLabel

DATASET_VERSION = "reply-intent-corpus-v1"


class SplitName(str, Enum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"


class EdgeCaseKind(str, Enum):
    """Why an example is hard, so metrics can be broken down by difficulty."""

    MULTI_INTENT = "multi_intent"
    AMBIGUOUS = "ambiguous"
    WRONG_INVOICE = "wrong_invoice"
    SARCASM = "sarcasm"
    NOISY_SHORTHAND = "noisy_shorthand"


class ReplyTemplate(BaseModel):
    """One phrasing, with its ground truth declared before any text exists."""

    model_config = ConfigDict(frozen=True)

    template_id: str
    intent: IntentLabel
    tone: str
    text: str
    date_style: str = "relative"
    dispute_reason: str | None = None
    edge_case_kind: EdgeCaseKind | None = None
    #: True when a "{days}" slot is phrased so it resolves to a real date
    #: ("in 30 days"), as opposed to a bare duration ("30 more days"). The
    #: extractor finds the former, so the ground truth has to record it.
    days_implies_date: bool = False

    @property
    def uses_amount(self) -> bool:
        return "{amount}" in self.text

    @property
    def uses_date(self) -> bool:
        return "{date}" in self.text

    @property
    def is_edge_case(self) -> bool:
        return self.edge_case_kind is not None


class LabelledReply(BaseModel):
    """One generated example with its label and entity ground truth."""

    model_config = ConfigDict(protected_namespaces=())

    reply_id: str
    invoice_id: str
    text: str
    intent: IntentLabel
    tone: str
    reference_date: date
    expected_amount: float | None = None
    expected_date: date | None = None
    currency: str = "INR"
    dispute_reason: str | None = None
    template_id: str
    edge_case_kind: EdgeCaseKind | None = None
    split: SplitName = SplitName.TRAIN

    @property
    def is_edge_case(self) -> bool:
        return self.edge_case_kind is not None


class ReplyCorpus(BaseModel):
    """A generated corpus plus the provenance needed to reproduce it."""

    model_config = ConfigDict(protected_namespaces=())

    dataset_version: str
    seed: int
    reference_date: date
    split_strategy: str
    examples: list[LabelledReply]

    def for_split(self, split: SplitName) -> list[LabelledReply]:
        return [example for example in self.examples if example.split is split]

    def intent_counts(self, split: SplitName | None = None) -> dict[str, int]:
        rows = self.examples if split is None else self.for_split(split)
        counts = {label.value: 0 for label in IntentLabel}
        for example in rows:
            counts[example.intent.value] += 1
        return counts


def _t(
    template_id: str,
    intent: IntentLabel,
    tone: str,
    text: str,
    *,
    date_style: str = "relative",
    dispute_reason: str | None = None,
    edge_case_kind: EdgeCaseKind | None = None,
    days_implies_date: bool = False,
) -> ReplyTemplate:
    return ReplyTemplate(
        template_id=template_id,
        intent=intent,
        tone=tone,
        text=text,
        date_style=date_style,
        dispute_reason=dispute_reason,
        edge_case_kind=edge_case_kind,
        days_implies_date=days_implies_date,
    )


_P = IntentLabel.PROMISE_TO_PAY
_D = IntentLabel.DISPUTE
_O = IntentLabel.OPT_OUT
_N = IntentLabel.NEGOTIATION_REQUEST
_PP = IntentLabel.PARTIAL_PAYMENT_CLAIM
_AP = IntentLabel.ALREADY_PAID_CLAIM
_Q = IntentLabel.GENERAL_QUERY
_X = IntentLabel.OTHER

#: 15 phrasings per class across the registers Indian B2B collections actually
#: sees: formal, curt, apologetic, annoyed, and Hinglish code-switching.
CORE_TEMPLATES: tuple[ReplyTemplate, ...] = (
    # --- PROMISE_TO_PAY -----------------------------------------------------
    _t(
        "p01",
        _P,
        "formal",
        "Thank you for the reminder on {invoice_id}. We will release the payment of {amount} {date}.",
        date_style="explicit",
    ),
    _t("p02", _P, "curt", "Will clear {amount} {date}."),
    _t(
        "p03",
        _P,
        "apologetic",
        "Apologies for the delay. Payment of {amount} will be made {date} without fail.",
        date_style="explicit",
    ),
    _t(
        "p04",
        _P,
        "hinglish",
        "Ji payment {date} tak ho jayega, {amount} transfer kar denge.",
        date_style="bare",
    ),
    _t(
        "p05",
        _P,
        "neutral",
        "Noted. Funds of {amount} are scheduled for release {date}.",
        date_style="explicit",
    ),
    _t(
        "p06",
        _P,
        "formal",
        "Kindly note that {invoice_id} has been approved for payment and will be settled {date}.",
        date_style="explicit",
    ),
    _t("p07", _P, "curt", "Paying {date}."),
    _t("p08", _P, "neutral", "We have queued {amount} for payment {date}."),
    _t(
        "p09",
        _P,
        "apologetic",
        "Sorry, our accounts team was on leave. We will remit {amount} {date}.",
    ),
    _t(
        "p10",
        _P,
        "formal",
        "The payment run is scheduled for {date} and your invoice is included for {amount}.",
        date_style="explicit",
    ),
    _t(
        "p11", _P, "hinglish", "Bhai {date} tak paisa aa jayega, tension mat lo.", date_style="bare"
    ),
    _t("p12", _P, "curt", "{amount} {date}. Confirmed."),
    _t(
        "p13",
        _P,
        "neutral",
        "Cheque for {amount} is being prepared and will be couriered {date}.",
        date_style="explicit",
    ),
    _t("p14", _P, "annoyed", "Fine, we will pay {date}. No need to chase every day."),
    _t(
        "p15",
        _P,
        "formal",
        "This is to confirm that {amount} against {invoice_id} will be transferred {date}.",
        date_style="explicit",
    ),
    # --- DISPUTE ------------------------------------------------------------
    _t(
        "d01",
        _D,
        "formal",
        "We cannot process {invoice_id}; the quantities billed do not match our goods receipt note.",
        dispute_reason="quantity_mismatch",
    ),
    _t(
        "d02",
        _D,
        "annoyed",
        "This invoice is wrong. You have charged freight twice and we never agreed to that.",
        dispute_reason="pricing_dispute",
    ),
    _t(
        "d03",
        _D,
        "curt",
        "Amount is incorrect. We are disputing this bill.",
        dispute_reason="pricing_dispute",
    ),
    _t(
        "d04",
        _D,
        "neutral",
        "There is a discrepancy in {invoice_id} - GST is charged at {pct}% but our contract says 12%.",
        dispute_reason="tax_discrepancy",
    ),
    _t(
        "d05",
        _D,
        "hinglish",
        "Bill mein galti hai, material short aaya tha aur poora amount charge kar diya.",
        dispute_reason="quantity_mismatch",
    ),
    _t(
        "d06",
        _D,
        "formal",
        "The consignment arrived damaged and has been rejected. Please issue a credit note.",
        dispute_reason="quality_issue",
    ),
    _t(
        "d07",
        _D,
        "neutral",
        "We have not received the goods against this invoice. Kindly confirm dispatch details.",
        dispute_reason="delivery_issue",
    ),
    _t(
        "d08",
        _D,
        "formal",
        "The rate applied does not match our purchase order. Please revise.",
        dispute_reason="pricing_dispute",
    ),
    _t(
        "d09",
        _D,
        "annoyed",
        "You have billed us twice for the same consignment. Sort this out.",
        dispute_reason="pricing_dispute",
    ),
    _t(
        "d10",
        _D,
        "curt",
        "TDS has not been deducted correctly on this bill.",
        dispute_reason="tax_discrepancy",
    ),
    _t(
        "d11",
        _D,
        "hinglish",
        "Maal kharab nikla, hum ye payment nahi karenge jab tak replacement nahi aata.",
        dispute_reason="quality_issue",
    ),
    _t(
        "d12",
        _D,
        "formal",
        "Our GRN shows a shortfall against {invoice_id}. Please share a revised invoice.",
        dispute_reason="grn_mismatch",
    ),
    _t(
        "d13",
        _D,
        "apologetic",
        "We would love to settle this but the delivery never reached our warehouse.",
        dispute_reason="delivery_issue",
    ),
    _t(
        "d14",
        _D,
        "neutral",
        "The unit price on this invoice is higher than what was quoted to us.",
        dispute_reason="pricing_dispute",
    ),
    _t(
        "d15",
        _D,
        "curt",
        "This is not per our agreement. Revise and resend.",
        dispute_reason="contract_mismatch",
    ),
    # --- OPT_OUT ------------------------------------------------------------
    _t("o01", _O, "curt", "Stop messaging me. Remove this number from your list."),
    _t("o02", _O, "formal", "Please unsubscribe us from these automated payment reminders."),
    _t("o03", _O, "annoyed", "Do not contact me again on WhatsApp."),
    _t("o04", _O, "neutral", "Kindly opt us out of this reminder channel."),
    _t("o05", _O, "hinglish", "Mujhe message mat bhejo, unsubscribe kar do."),
    _t("o06", _O, "curt", "Take me off your mailing list."),
    _t("o07", _O, "curt", "Stop sending these texts."),
    _t("o08", _O, "neutral", "Remove my email from your system."),
    _t("o09", _O, "annoyed", "Do not call this number again."),
    _t(
        "o10",
        _O,
        "formal",
        "Please stop contacting us here; write to our registered office instead.",
    ),
    _t("o11", _O, "curt", "Unsubscribe."),
    _t("o12", _O, "hinglish", "Band karo ye messages."),
    _t("o13", _O, "formal", "We do not wish to receive automated reminders. Remove our details."),
    _t("o14", _O, "annoyed", "Stop these reminders immediately."),
    _t("o15", _O, "neutral", "Do not message me, I am not the right contact for this."),
    # --- NEGOTIATION_REQUEST ------------------------------------------------
    _t("n01", _N, "curt", "Will clear {amount} {date}, waive the late fee please."),
    _t(
        "n02",
        _N,
        "formal",
        "Cash flow is tight this quarter. Can we settle {invoice_id} in three monthly instalments?",
    ),
    _t(
        "n03",
        _N,
        "apologetic",
        "We would like to request an extension of {days} days on this invoice.",
    ),
    _t(
        "n04",
        _N,
        "neutral",
        "If you can offer a {pct}% early settlement discount we can arrange the funds this week.",
    ),
    _t(
        "n05",
        _N,
        "hinglish",
        "Thoda time chahiye, EMI mein kar sakte hain kya? Interest mat lagana.",
    ),
    _t(
        "n06",
        _N,
        "formal",
        "Would you consider a part payment now and the balance in {days} days?",
        days_implies_date=True,
    ),
    _t("n07", _N, "curt", "Give us {days} more days and we will clear it."),
    _t("n08", _N, "neutral", "We can pay {amount} now if you drop the interest charges."),
    _t(
        "n09",
        _N,
        "formal",
        "Requesting a revised payment schedule for {invoice_id} spread over two months.",
    ),
    _t("n10", _N, "apologetic", "Please waive the penalty and we will settle {date}."),
    _t(
        "n11",
        _N,
        "hinglish",
        "Late fee hata do, {date} tak full payment kar denge.",
        date_style="bare",
    ),
    _t("n12", _N, "neutral", "Can we agree on {amount} as a full and final settlement?"),
    _t("n13", _N, "curt", "Need more time. {days} days."),
    _t("n14", _N, "formal", "We propose to clear this in two tranches, subject to your approval."),
    _t("n15", _N, "neutral", "Happy to pay {date} provided the late fee is reversed."),
    # --- PARTIAL_PAYMENT_CLAIM ----------------------------------------------
    _t(
        "m01",
        _PP,
        "neutral",
        "We have already transferred {amount} against this invoice, the balance will follow shortly.",
    ),
    _t(
        "m02",
        _PP,
        "formal",
        "A part payment of {amount} was remitted towards {invoice_id}. Kindly advise the remaining balance.",
    ),
    _t("m03", _PP, "curt", "Paid {amount} already. Rest later."),
    _t(
        "m04",
        _PP,
        "apologetic",
        "We could only manage {amount} this month, sorry. The remainder follows next cycle.",
    ),
    _t("m05", _PP, "hinglish", "{amount} to bhej diya hai, baaki agle hafte kar denge."),
    _t("m06", _PP, "neutral", "{amount} has been paid and the balance is under approval."),
    _t(
        "m07", _PP, "formal", "Please note {amount} was released last week as a partial settlement."
    ),
    _t("m08", _PP, "curt", "Half paid. {amount} done."),
    _t(
        "m09",
        _PP,
        "neutral",
        "We have cleared {amount} of this invoice and will clear the rest {date}.",
    ),
    _t("m10", _PP, "hinglish", "Thoda payment ho gaya hai, {amount} transfer kiya tha."),
    _t("m11", _PP, "formal", "Partial remittance of {amount} has been made against this account."),
    _t(
        "m12",
        _PP,
        "apologetic",
        "Only {amount} could be released, our apologies for the shortfall.",
    ),
    _t("m13", _PP, "neutral", "The first instalment of {amount} is done."),
    _t("m14", _PP, "curt", "{amount} sent, balance pending."),
    _t("m15", _PP, "formal", "Kindly adjust {amount} already received against {invoice_id}."),
    # --- ALREADY_PAID_CLAIM -------------------------------------------------
    _t("a01", _AP, "curt", "This is already paid. Check your bank statement."),
    _t(
        "a02",
        _AP,
        "formal",
        "Payment against {invoice_id} was settled by NEFT {date}. The UTR has been shared with your accounts team.",
        date_style="past_explicit",
    ),
    _t(
        "a03",
        _AP,
        "annoyed",
        "We paid this weeks ago. Reconcile your books before sending reminders.",
    ),
    _t("a04", _AP, "neutral", "Our records show this invoice was cleared in full."),
    _t("a05", _AP, "hinglish", "Payment ho chuka hai, humne pichle hafte hi transfer kiya tha."),
    _t(
        "a06",
        _AP,
        "formal",
        "The full amount was remitted {date}; the payment advice is attached.",
        date_style="past_explicit",
    ),
    _t("a07", _AP, "curt", "Settled already."),
    _t("a08", _AP, "neutral", "This was paid along with the previous batch."),
    _t("a09", _AP, "annoyed", "Again? We have already cleared this invoice."),
    _t(
        "a10",
        _AP,
        "formal",
        "Kindly check with your bank; the transfer was completed {date}.",
        date_style="past_explicit",
    ),
    _t("a11", _AP, "hinglish", "Ye bill to clear ho gaya tha, apne records check karo."),
    _t("a12", _AP, "neutral", "The payment reference has already been shared with your team."),
    _t("a13", _AP, "curt", "Paid in full."),
    _t(
        "a14",
        _AP,
        "formal",
        "We consider this account settled as of {date}.",
        date_style="past_explicit",
    ),
    _t(
        "a15",
        _AP,
        "neutral",
        "The amount was debited from our account {date}.",
        date_style="past_explicit",
    ),
    # --- GENERAL_QUERY ------------------------------------------------------
    _t("q01", _Q, "neutral", "Could you share the GST breakup for {invoice_id}?"),
    _t("q02", _Q, "formal", "Kindly confirm the bank account details for remittance."),
    _t("q03", _Q, "curt", "Which PO is this invoice against?"),
    _t("q04", _Q, "neutral", "Can you resend the invoice copy? We cannot locate it."),
    _t("q05", _Q, "hinglish", "Invoice ki soft copy dobara bhej dena, mail mein mil nahi rahi."),
    _t("q06", _Q, "formal", "Please share the statement of account for this quarter."),
    _t("q07", _Q, "curt", "What is the due date on this?"),
    _t("q08", _Q, "neutral", "Who should we contact regarding this invoice?"),
    _t("q09", _Q, "formal", "Could you confirm whether TDS has been considered in this amount?"),
    _t("q10", _Q, "curt", "Is this inclusive of freight?"),
    _t("q11", _Q, "neutral", "Please share the e-way bill reference."),
    _t("q12", _Q, "hinglish", "Ye invoice kis order ka hai?"),
    _t("q13", _Q, "formal", "Kindly provide a duplicate copy for our records."),
    _t("q14", _Q, "neutral", "Do you accept UPI for this payment?"),
    _t("q15", _Q, "curt", "Send the ledger."),
    # --- OTHER --------------------------------------------------------------
    _t("x01", _X, "curt", "Ok."),
    _t("x02", _X, "neutral", "Received, thanks."),
    _t("x03", _X, "neutral", "Our office is closed for Diwali until next week."),
    _t("x04", _X, "curt", "Wrong number."),
    _t("x05", _X, "neutral", "Please speak to Mr. Ramesh, he handles this account now."),
    _t("x06", _X, "formal", "Noted with thanks."),
    _t("x07", _X, "neutral", "I am on leave this week and will revert."),
    _t("x08", _X, "curt", "This is not my department."),
    _t("x09", _X, "hinglish", "Theek hai."),
    _t("x10", _X, "neutral", "Forwarding this to our accounts team."),
    _t("x11", _X, "formal", "Wishing you and the team a happy new year."),
    _t("x12", _X, "neutral", "Thanks for the update."),
    _t("x13", _X, "curt", "K"),
    _t("x14", _X, "neutral", "Please call after 5 pm."),
    _t(
        "x15",
        _X,
        "formal",
        "Our GST number has changed; we will share the updated details separately.",
    ),
)

#: Deliberately hard cases. Each is labelled by the disambiguation rules in
#: ``prompts.py``, so the dataset and the LLM prompt agree on the edges.
EDGE_CASE_TEMPLATES: tuple[ReplyTemplate, ...] = (
    # Multi-intent: a dispute alongside a commitment. Dispute wins, because the
    # agent must stop escalating a customer who is contesting the charge.
    _t(
        "e01",
        _D,
        "neutral",
        "We will pay, but this invoice has an error in the quantity.",
        dispute_reason="quantity_mismatch",
        edge_case_kind=EdgeCaseKind.MULTI_INTENT,
    ),
    _t(
        "e02",
        _D,
        "annoyed",
        "Payment will come once you fix the rate, which is wrong on {invoice_id}.",
        dispute_reason="pricing_dispute",
        edge_case_kind=EdgeCaseKind.MULTI_INTENT,
    ),
    # Opt-out always wins, even alongside a dispute or a promise.
    _t(
        "e03",
        _O,
        "annoyed",
        "Please stop messaging me, and also this bill is wrong.",
        edge_case_kind=EdgeCaseKind.MULTI_INTENT,
    ),
    _t(
        "e04",
        _O,
        "curt",
        "We will pay next week. Do not message me again about it.",
        edge_case_kind=EdgeCaseKind.MULTI_INTENT,
    ),
    # A conditional commitment is a negotiation, not a promise.
    _t(
        "e05",
        _N,
        "neutral",
        "We paid part of it and will clear the rest {date}, but waive the interest.",
        edge_case_kind=EdgeCaseKind.MULTI_INTENT,
    ),
    _t(
        "e06",
        _AP,
        "curt",
        "Already paid, but send me the ledger anyway.",
        edge_case_kind=EdgeCaseKind.MULTI_INTENT,
    ),
    # Ambiguous: vague non-commitments that look like promises.
    _t("e07", _X, "neutral", "Let me check and get back.", edge_case_kind=EdgeCaseKind.AMBIGUOUS),
    _t("e08", _X, "curt", "We will see.", edge_case_kind=EdgeCaseKind.AMBIGUOUS),
    _t("e09", _X, "neutral", "Maybe next month.", edge_case_kind=EdgeCaseKind.AMBIGUOUS),
    _t("e10", _X, "curt", "Under process.", edge_case_kind=EdgeCaseKind.AMBIGUOUS),
    # Wrong invoice: the customer says the invoice is not theirs at all.
    _t(
        "e11",
        _D,
        "annoyed",
        "This does not belong to us, we never ordered from you. Check {other_invoice_id}.",
        dispute_reason="contract_mismatch",
        edge_case_kind=EdgeCaseKind.WRONG_INVOICE,
    ),
    _t(
        "e12",
        _D,
        "neutral",
        "You have sent someone else's invoice; {other_invoice_id} is not ours.",
        dispute_reason="contract_mismatch",
        edge_case_kind=EdgeCaseKind.WRONG_INVOICE,
    ),
    # Sarcasm: positive-sounding words, no commitment.
    _t(
        "e13",
        _X,
        "annoyed",
        "Wonderful, another reminder. Truly the highlight of my week.",
        edge_case_kind=EdgeCaseKind.SARCASM,
    ),
    _t(
        "e14",
        _X,
        "annoyed",
        "Sure, we are made of money. We will pay when we can.",
        edge_case_kind=EdgeCaseKind.SARCASM,
    ),
    _t(
        "e15",
        _X,
        "annoyed",
        "Great service, and now you want money too.",
        edge_case_kind=EdgeCaseKind.SARCASM,
    ),
    # SMS shorthand.
    _t("e16", _Q, "curt", "pls snd inv copy", edge_case_kind=EdgeCaseKind.NOISY_SHORTHAND),
    _t(
        "e17",
        _N,
        "curt",
        "will clr {amount} {date} pls waive lt fee",
        edge_case_kind=EdgeCaseKind.NOISY_SHORTHAND,
    ),
    _t("e18", _AP, "curt", "PAYMENT DONE!!!", edge_case_kind=EdgeCaseKind.NOISY_SHORTHAND),
)

ALL_TEMPLATES: tuple[ReplyTemplate, ...] = CORE_TEMPLATES + EDGE_CASE_TEMPLATES


# ---------------------------------------------------------------------------
# Surface noise
# ---------------------------------------------------------------------------

_GREETINGS: tuple[str, ...] = ("Hi", "Hello", "Dear Sir", "Sir", "Hello ji", "Dear Madam")
_SIGNOFFS: tuple[str, ...] = (
    "Thanks",
    "Regards",
    "Thanks and regards",
    "- Ramesh",
    "- Accounts Dept",
    "Best",
)
_ABBREVIATIONS: tuple[tuple[str, str], ...] = (
    ("please", "pls"),
    ("Please", "Pls"),
    ("thanks", "thx"),
    ("and", "n"),
    ("you", "u"),
    ("your", "ur"),
    ("we will", "we'll"),
    ("cannot", "cant"),
)


def _apply_surface_noise(text: str, rng: np.random.Generator) -> str:
    """Rough the text up the way a real inbox would.

    None of these change the meaning, so the label stays correct while the
    surface form varies -- which is what stops the classifier from keying on
    template punctuation.
    """

    if rng.random() < 0.18:
        for long_form, short_form in _ABBREVIATIONS:
            if long_form in text and rng.random() < 0.5:
                text = text.replace(long_form, short_form)

    if rng.random() < 0.14:
        greeting = _GREETINGS[int(rng.integers(len(_GREETINGS)))]
        text = f"{greeting}, {text[0].lower() + text[1:] if text else text}"

    if rng.random() < 0.12:
        text = f"{text} {_SIGNOFFS[int(rng.integers(len(_SIGNOFFS)))]}"

    if rng.random() < 0.10:
        text = text.lower()

    if rng.random() < 0.08:
        text = text.rstrip(".!? ")

    if rng.random() < 0.06:
        words = text.split()
        long_words = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
        if long_words:
            index = long_words[int(rng.integers(len(long_words)))]
            word = words[index]
            position = int(rng.integers(1, len(word) - 1))
            words[index] = (
                word[:position] + word[position + 1] + word[position] + word[position + 2 :]
            )
            text = " ".join(words)

    return text


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _render(
    template: ReplyTemplate,
    rng: np.random.Generator,
    reference: date,
    invoice_id: str,
) -> tuple[str, float | None, date | None]:
    amount_value: int | None = None
    amount_text = ""
    if template.uses_amount:
        amount_value = draw_amount(rng)
        amount_text = render_amount(rng, amount_value)

    date_value: date | None = None
    date_text = ""
    if template.uses_date:
        date_text, date_value = render_date_phrase(rng, reference, template.date_style)

    other_invoice_id = f"INV-{reference.year}-{int(rng.integers(10000, 99999)):05d}"
    days = int(rng.integers(7, 46))
    if template.days_implies_date:
        date_value = reference + timedelta(days=days)

    text = template.text.format(
        invoice_id=invoice_id,
        other_invoice_id=other_invoice_id,
        amount=amount_text,
        date=date_text,
        days=days,
        pct=int(rng.integers(2, 19)),
    )
    text = _apply_surface_noise(text, rng)
    return text, (float(amount_value) if amount_value is not None else None), date_value


def _stratified_template_split(
    templates: Sequence[ReplyTemplate],
    rng: np.random.Generator,
    ratios: tuple[float, float, float],
) -> dict[str, SplitName]:
    """Assign whole templates to splits, stratified by intent.

    No template appears in two splits, so a test score cannot come from having
    memorised the phrasing.
    """

    train_ratio, val_ratio, _ = ratios
    assignment: dict[str, SplitName] = {}

    by_intent: dict[IntentLabel, list[ReplyTemplate]] = {}
    for template in templates:
        by_intent.setdefault(template.intent, []).append(template)

    for intent_templates in by_intent.values():
        ordered = sorted(intent_templates, key=lambda t: t.template_id)
        indices = rng.permutation(len(ordered))
        total = len(ordered)
        # At least one template per split whenever there are enough to go round.
        n_train = max(1, int(round(total * train_ratio)))
        n_val = max(1, int(round(total * val_ratio))) if total >= 3 else 0
        n_train = min(n_train, total - (1 if total >= 2 else 0) - (1 if total >= 3 else 0))

        for rank, index in enumerate(indices):
            if rank < n_train:
                split = SplitName.TRAIN
            elif rank < n_train + n_val:
                split = SplitName.VAL
            else:
                split = SplitName.TEST
            assignment[ordered[index].template_id] = split

    return assignment


def build_corpus(
    *,
    size: int = 1200,
    seed: int = 42,
    reference_date: date | None = None,
    split_strategy: str = "grouped",
    ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
    edge_case_fraction: float = 0.12,
) -> ReplyCorpus:
    """Generate a labelled corpus of ``size`` examples.

    ``split_strategy`` is ``"grouped"`` (no template spans two splits -- the
    honest measurement) or ``"random"`` (the conventional stratified split,
    kept for comparison; it will score higher for the wrong reason).
    """

    if size < len(ALL_TEMPLATES):
        raise ValueError(f"size must be at least {len(ALL_TEMPLATES)} to cover every template")
    if split_strategy not in {"grouped", "random"}:
        raise ValueError("split_strategy must be 'grouped' or 'random'")
    if not 0.0 <= edge_case_fraction < 1.0:
        raise ValueError("edge_case_fraction must be in [0, 1)")

    rng = make_rng(seed)
    reference = reference_date or date(2026, 9, 1)

    grouped_assignment = (
        _stratified_template_split(ALL_TEMPLATES, rng, ratios)
        if split_strategy == "grouped"
        else {}
    )

    # Balance the class distribution: every intent gets an equal share, so rare
    # classes such as OPT_OUT are not starved.
    intents = list(IntentLabel)
    per_intent = size // len(intents)
    remainder = size - per_intent * len(intents)

    core_by_intent: dict[IntentLabel, list[ReplyTemplate]] = {}
    edge_by_intent: dict[IntentLabel, list[ReplyTemplate]] = {}
    for template in CORE_TEMPLATES:
        core_by_intent.setdefault(template.intent, []).append(template)
    for template in EDGE_CASE_TEMPLATES:
        edge_by_intent.setdefault(template.intent, []).append(template)

    examples: list[LabelledReply] = []
    counter = 0

    for position, intent in enumerate(intents):
        target = per_intent + (1 if position < remainder else 0)
        core_pool = core_by_intent.get(intent, [])
        edge_pool = edge_by_intent.get(intent, [])

        for _ in range(target):
            use_edge = bool(edge_pool) and rng.random() < edge_case_fraction
            pool = edge_pool if use_edge else core_pool
            if not pool:
                pool = core_pool or edge_pool
            template = pool[int(rng.integers(len(pool)))]

            counter += 1
            invoice_id = f"INV-{reference.year}-{counter:05d}"
            text, amount, resolved_date = _render(template, rng, reference, invoice_id)

            examples.append(
                LabelledReply(
                    reply_id=f"rpl_{counter:05d}",
                    invoice_id=invoice_id,
                    text=text,
                    intent=template.intent,
                    tone=template.tone,
                    reference_date=reference,
                    expected_amount=amount,
                    expected_date=resolved_date,
                    dispute_reason=template.dispute_reason,
                    template_id=template.template_id,
                    edge_case_kind=template.edge_case_kind,
                    split=grouped_assignment.get(template.template_id, SplitName.TRAIN),
                )
            )

    if split_strategy == "random":
        _assign_random_splits(examples, rng, ratios)

    return ReplyCorpus(
        dataset_version=DATASET_VERSION,
        seed=seed,
        reference_date=reference,
        split_strategy=split_strategy,
        examples=examples,
    )


def _assign_random_splits(
    examples: list[LabelledReply],
    rng: np.random.Generator,
    ratios: tuple[float, float, float],
) -> None:
    """Conventional stratified 70/15/15 split, assigned in place."""

    train_ratio, val_ratio, _ = ratios
    by_intent: dict[IntentLabel, list[int]] = {}
    for index, example in enumerate(examples):
        by_intent.setdefault(example.intent, []).append(index)

    for indices in by_intent.values():
        order = rng.permutation(len(indices))
        n_train = int(round(len(indices) * train_ratio))
        n_val = int(round(len(indices) * val_ratio))
        for rank, offset in enumerate(order):
            if rank < n_train:
                split = SplitName.TRAIN
            elif rank < n_train + n_val:
                split = SplitName.VAL
            else:
                split = SplitName.TEST
            examples[indices[offset]].split = split


# ---------------------------------------------------------------------------
# Quality audit
# ---------------------------------------------------------------------------


#: Looser than the binding opt-out guard in ``service.py`` on purpose: this is
#: a mislabelling check, so it must survive the typos the noise transform adds.
_OPT_OUT_CUE_RE = re.compile(
    r"\bunsubscrib\w*|\bstop\b|\bremove\b|\bopt\b|\bdo\s*n[o']?t\b|\bdont\b"
    r"|\bnever\b|\bmat\b|\bband\b|\boff\b|\bnot wish\b",
    re.IGNORECASE,
)


class AuditFinding(BaseModel):
    """One example that failed a label-consistency check."""

    reply_id: str
    template_id: str
    intent: IntentLabel
    text: str
    problem: str


class AuditReport(BaseModel):
    """Automated consistency audit over a sample of the corpus.

    This does **not** replace the manual spot-check the plan calls for. It
    catches the mechanical failures -- an entity that cannot be recovered, an
    opt-out phrase inside a non-opt-out example -- and writes the sample to
    disk so a human can read it. The human pass is still a human's to do.
    """

    model_config = ConfigDict(protected_namespaces=())

    sampled: int
    total: int
    sample_fraction: float
    findings: list[AuditFinding] = Field(default_factory=list)

    #: OPT_OUT examples the deterministic guard in ``service.py`` would miss --
    #: typically because a typo mangled the trigger word. Not a fault: these are
    #: precisely the cases the trained classifier has to catch on its own, and a
    #: corpus with none of them would overstate how well the regex covers
    #: opt-out. Reported so the number stays visible.
    opt_out_guard_misses: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings


def audit_corpus(
    corpus: ReplyCorpus,
    *,
    sample_fraction: float = 0.10,
    seed: int = 7,
) -> tuple[AuditReport, list[LabelledReply]]:
    """Check a sample of the corpus for mechanical labelling faults.

    Returns the report and the sampled examples, so the sample can be written
    out for human review.
    """

    from src.ml.reply.entity_extraction import extract_amount, extract_date
    from src.ml.reply.service import looks_like_opt_out

    if not 0.0 < sample_fraction <= 1.0:
        raise ValueError("sample_fraction must be in (0, 1]")

    rng = make_rng(seed)
    total = len(corpus.examples)
    sample_size = max(1, int(round(total * sample_fraction)))
    indices = rng.choice(total, size=sample_size, replace=False)
    sample = [corpus.examples[int(i)] for i in sorted(indices)]

    findings: list[AuditFinding] = []
    guard_misses = 0
    for example in sample:
        problems: list[str] = []

        opt_out_phrasing = looks_like_opt_out(example.text)
        if opt_out_phrasing and example.intent is not IntentLabel.OPT_OUT:
            problems.append("contains an explicit stop-contact phrase but is not labelled OPT_OUT")
        if example.intent is IntentLabel.OPT_OUT:
            if not opt_out_phrasing:
                guard_misses += 1
            # The strict guard is regex-exact, so a typo defeats it. Mislabelling
            # is the thing worth flagging, so check for opt-out *cues* instead.
            if not _OPT_OUT_CUE_RE.search(example.text):
                problems.append("labelled OPT_OUT but contains no opt-out cue at all")

        if (
            example.expected_amount is not None
            and extract_amount(example.text) != example.expected_amount
        ):
            problems.append("declared amount is not recoverable from the text")

        if example.expected_date is not None:
            reference_dt = _as_datetime(example.reference_date)
            if extract_date(example.text, reference_dt) != example.expected_date:
                problems.append("declared date is not recoverable from the text")

        if example.intent is IntentLabel.DISPUTE and example.dispute_reason is None:
            problems.append("DISPUTE example carries no dispute_reason")

        if not example.text.strip():
            problems.append("empty text")

        for problem in problems:
            findings.append(
                AuditFinding(
                    reply_id=example.reply_id,
                    template_id=example.template_id,
                    intent=example.intent,
                    text=example.text,
                    problem=problem,
                )
            )

    report = AuditReport(
        sampled=len(sample),
        total=total,
        sample_fraction=sample_fraction,
        findings=findings,
        opt_out_guard_misses=guard_misses,
    )
    return report, sample


def _as_datetime(value: date) -> Any:
    from datetime import datetime

    return datetime.combine(value, datetime.min.time())


def corpus_texts_and_labels(
    examples: Sequence[LabelledReply],
) -> tuple[list[str], list[str]]:
    """Split a list of examples into the X and y scikit-learn expects."""

    return [e.text for e in examples], [e.intent.value for e in examples]
