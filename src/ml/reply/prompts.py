"""Prompt construction for the LLM reply-understanding baseline.

Kept separate from the call site so the prompt can be reviewed, diffed and
version-stamped on its own. ``PROMPT_VERSION`` is written into the prediction's
``model_version``, so a prediction can always be traced back to the exact
wording that produced it.
"""

import json
from typing import Any

from src.ml.schemas import IntentLabel

PROMPT_VERSION = "reply-intent-fewshot-v1"

#: What each label means, and -- more usefully -- where its edges are.
INTENT_DEFINITIONS: dict[IntentLabel, str] = {
    IntentLabel.PROMISE_TO_PAY: (
        "The customer commits to paying, with or without a specific date or amount. "
        "Use this only when the commitment is unconditional."
    ),
    IntentLabel.DISPUTE: (
        "The customer contests the invoice itself -- wrong amount, wrong quantity, "
        "wrong tax, duplicate charge, goods not received or not acceptable."
    ),
    IntentLabel.OPT_OUT: (
        "The customer asks to stop being contacted on this channel: unsubscribe, "
        "stop messaging, remove my number, do not contact me."
    ),
    IntentLabel.NEGOTIATION_REQUEST: (
        "The customer asks to change the terms before paying -- an extension, "
        "instalments, a discount, or a fee waiver. If a payment commitment is "
        "conditional on the seller agreeing to something, it is a negotiation, "
        "not a promise."
    ),
    IntentLabel.PARTIAL_PAYMENT_CLAIM: (
        "The customer states that part of the invoice has already been paid and a "
        "balance remains."
    ),
    IntentLabel.ALREADY_PAID_CLAIM: (
        "The customer states the invoice has been settled in full already."
    ),
    IntentLabel.GENERAL_QUERY: (
        "The customer asks for information -- a copy of the invoice, bank details, "
        "a PO reference, a tax breakup -- without disputing or committing."
    ),
    IntentLabel.OTHER: (
        "Anything else: acknowledgements, out-of-office notes, wrong number, "
        "handover to a colleague, or text with no discernible collections intent."
    ),
}

#: The confusions that actually happen, stated as rules rather than left to
#: the model's judgement.
DISAMBIGUATION_RULES: tuple[str, ...] = (
    "A commitment with a condition attached ('we'll pay if you waive the late fee') "
    "is NEGOTIATION_REQUEST, not PROMISE_TO_PAY.",
    "'We already paid part of it, rest next week' is PARTIAL_PAYMENT_CLAIM, not "
    "PROMISE_TO_PAY, even though it contains a future commitment.",
    "Any request to stop contact is OPT_OUT, even if the same message also "
    "disputes the invoice. Opt-out always wins.",
    "A complaint about service or delay that does not contest the invoice amount "
    "is not a DISPUTE.",
    "Replies may be in English, Hindi-English (Hinglish) or transliterated Hindi. "
    "Classify on meaning, not language.",
)

#: Few-shot examples covering all eight classes, including the two hard cases
#: (conditional commitment, and opt-out combined with a dispute).
FEW_SHOT_EXAMPLES: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "Will clear Rs. 45,000 by next Friday.",
        {
            "intent": "PROMISE_TO_PAY",
            "confidence": 0.95,
            "explanation": "Unconditional commitment with an amount and a date.",
            "promised_amount": 45000,
            "promised_date": "next Friday",
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
    (
        "will clear 1.8L by fri, waive the late fee pls",
        {
            "intent": "NEGOTIATION_REQUEST",
            "confidence": 0.82,
            "explanation": "Payment is offered conditional on the late fee being waived.",
            "promised_amount": 180000,
            "promised_date": "friday",
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
    (
        "The quantities billed do not match our goods receipt note. Please send a corrected invoice.",
        {
            "intent": "DISPUTE",
            "confidence": 0.93,
            "explanation": "Contests the billed quantity against the GRN.",
            "promised_amount": None,
            "promised_date": None,
            "currency": "INR",
            "dispute_reason": "quantity_mismatch",
        },
    ),
    (
        "Stop messaging me. Remove this number from your list.",
        {
            "intent": "OPT_OUT",
            "confidence": 0.98,
            "explanation": "Explicit request to stop contact on this channel.",
            "promised_amount": None,
            "promised_date": None,
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
    (
        "We have already transferred 60k against this invoice, the balance will follow shortly.",
        {
            "intent": "PARTIAL_PAYMENT_CLAIM",
            "confidence": 0.9,
            "explanation": "States a part payment was made with a balance outstanding.",
            "promised_amount": 60000,
            "promised_date": None,
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
    (
        "Payment ho chuka hai, humne pichle hafte hi transfer kiya tha.",
        {
            "intent": "ALREADY_PAID_CLAIM",
            "confidence": 0.91,
            "explanation": "Hinglish: claims the invoice was already paid last week.",
            "promised_amount": None,
            "promised_date": None,
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
    (
        "Could you share the GST breakup for this invoice?",
        {
            "intent": "GENERAL_QUERY",
            "confidence": 0.94,
            "explanation": "Requests information; no commitment and no dispute.",
            "promised_amount": None,
            "promised_date": None,
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
    (
        "Please speak to Mr. Ramesh, he handles this account now.",
        {
            "intent": "OTHER",
            "confidence": 0.87,
            "explanation": "Contact handover; no collections intent.",
            "promised_amount": None,
            "promised_date": None,
            "currency": "INR",
            "dispute_reason": None,
        },
    ),
)

SYSTEM_PROMPT = """You classify replies that Indian B2B customers send in response to \
invoice payment reminders. You return JSON only -- no prose, no markdown fences, no \
explanation outside the JSON object.

You never decide what action to take. You only report what the customer said."""


def _format_intent_catalogue() -> str:
    lines = [f"- {label.value}: {INTENT_DEFINITIONS[label]}" for label in IntentLabel]
    return "\n".join(lines)


def _format_rules() -> str:
    return "\n".join(f"- {rule}" for rule in DISAMBIGUATION_RULES)


def _format_examples() -> str:
    blocks = []
    for text, output in FEW_SHOT_EXAMPLES:
        blocks.append(f"Reply: {text}\nJSON: {json.dumps(output, ensure_ascii=False)}")
    return "\n\n".join(blocks)


def _format_invoice_context(invoice_context: dict[str, Any] | None) -> str:
    if not invoice_context:
        return "(no invoice context available)"
    lines = [f"- {key}: {value}" for key, value in sorted(invoice_context.items())]
    return "\n".join(lines)


def build_classification_prompt(
    raw_text: str,
    invoice_context: dict[str, Any] | None = None,
    *,
    strict: bool = False,
) -> str:
    """Build the reply-classification prompt.

    ``strict=True`` is the retry form used after a malformed response: it drops
    the discussion and repeats the output contract in blunter terms.
    """

    contract = (
        "Return a single JSON object with exactly these keys:\n"
        '  "intent": one of ' + ", ".join(label.value for label in IntentLabel) + "\n"
        '  "confidence": a number between 0 and 1\n'
        '  "explanation": one short sentence quoting the decisive words\n'
        '  "promised_amount": a number, or null\n'
        '  "promised_date": the date phrase exactly as the customer wrote it, or null\n'
        '  "currency": a 3-letter code, default "INR"\n'
        '  "dispute_reason": a short snake_case reason, or null'
    )

    if strict:
        return (
            "Return ONLY valid JSON matching this contract. No markdown, no code "
            "fences, no text before or after the object.\n\n"
            f"{contract}\n\n"
            f"Reply to classify:\n{raw_text}\n\nJSON:"
        )

    return (
        "Classify the customer reply below.\n\n"
        f"INTENT CLASSES\n{_format_intent_catalogue()}\n\n"
        f"RULES\n{_format_rules()}\n\n"
        f"OUTPUT CONTRACT\n{contract}\n\n"
        f"EXAMPLES\n{_format_examples()}\n\n"
        f"INVOICE CONTEXT\n{_format_invoice_context(invoice_context)}\n\n"
        f"Reply to classify:\n{raw_text}\n\nJSON:"
    )
