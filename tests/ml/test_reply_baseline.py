import asyncio
from datetime import date, datetime

import pytest

from src.data.synthetic_generator import generate_batch
from src.ml.reply.entity_extraction import (
    extract_amount,
    extract_date,
    extract_dispute_reason,
    extract_entities,
    normalize_currency,
)
from src.ml.reply.llm_baseline import (
    MODEL_USED,
    ReplyIntentLLMOutput,
    classify_reply_llm,
)
from src.ml.reply.prompts import PROMPT_VERSION, build_classification_prompt
from src.ml.reply.service import (
    ENTITY_BEARING_INTENTS,
    looks_like_opt_out,
    set_primary_classifier,
    understand_reply,
    understand_reply_sync,
)
from src.ml.schemas import FallbackReason, IntentLabel, ReplyIntentPrediction

REFERENCE = datetime(2026, 9, 1)  # a Tuesday


def run(coro):
    """Run one coroutine. Avoids a hard dependency on an async pytest plugin."""

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class StubLLM:
    """A structured-output client that returns a scripted result."""

    def __init__(self, output=None, *, error: Exception | None = None, delay: float = 0.0):
        self.output = output
        self.error = error
        self.delay = delay
        self.calls: list[str] = []

    async def generate_structured(
        self, prompt, schema, system_prompt=None, retry_prompt=None
    ):
        self.calls.append(prompt)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.output


def llm_output(intent: IntentLabel, **overrides) -> ReplyIntentLLMOutput:
    payload = {
        "intent": intent,
        "confidence": 0.9,
        "explanation": f"Scripted {intent.value}.",
    }
    payload.update(overrides)
    return ReplyIntentLLMOutput(**payload)


@pytest.fixture(autouse=True)
def restore_primary_classifier():
    """Keep the module-level classifier binding from leaking between tests."""

    from src.ml.reply import service

    original = service.primary_classifier
    yield
    service.primary_classifier = original


@pytest.fixture(scope="module")
def seed_examples():
    return generate_batch(batch_size=600, seed=42).reply_seed_examples


# ---------------------------------------------------------------------------
# Amount extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Will clear ₹45,000 by Friday.", 45000.0),
        ("Payment of Rs. 1,80,000 released.", 180000.0),
        ("INR 45000 transferred.", 45000.0),
        ("will clear 1.8L by fri", 180000.0),
        ("we can do 45k this week", 45000.0),
        ("Total is 2 crore for the year.", 20000000.0),
        ("₹2.5 lakh has been sent.", 250000.0),
        ("Paid 1,20,000 already.", 120000.0),
        ("Transferred 180,000 yesterday.", 180000.0),
        ("Amount 45000 paid.", 45000.0),
        ("Balance of Rs 12,500.50 pending.", 12500.5),
        ("1.25 cr sanctioned.", 12500000.0),
    ],
)
def test_extract_amount_handles_indian_money_formats(text, expected):
    assert extract_amount(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The GST rate applied is 18% but our contract says 12%.",
        "We request an extension of 30 days on this invoice.",
        "Can we settle in three monthly instalments?",
        "Please share the copy of invoice INV-2026-00022.",
        "Payment was settled by NEFT on 22 August 2026.",
        "Our PO 4471 covers this.",
        "Ok.",
        "",
    ],
)
def test_extract_amount_rejects_things_that_are_not_money(text):
    assert extract_amount(text) is None


def test_currency_marked_amount_wins_over_a_bare_number():
    text = "Against 3 invoices we are paying Rs. 90,000 today."

    assert extract_amount(text) == 90000.0


def test_percentage_next_to_an_amount_is_not_the_amount():
    text = "If you give a 5% discount we will pay ₹95,000 immediately."

    assert extract_amount(text) == 95000.0


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We will pay on 23 September 2026.", date(2026, 9, 23)),
        ("Payment by 13-09-2026 without fail.", date(2026, 9, 13)),
        ("Settled on 22 August 2026.", date(2026, 8, 22)),
        ("Will clear 08/09/2026.", date(2026, 9, 8)),
        ("Will clear tomorrow.", date(2026, 9, 2)),
        ("Payment in 3 days.", date(2026, 9, 4)),
        ("We will pay by next Friday.", date(2026, 9, 4)),
        ("Payment Thursday tak ho jayega.", date(2026, 9, 3)),
        ("Funds within 2 weeks.", date(2026, 9, 15)),
        ("Paid the day after tomorrow.", date(2026, 9, 3)),
        ("We will settle at the end of the month.", date(2026, 9, 30)),
        ("Payment will be done by month end.", date(2026, 9, 30)),
    ],
)
def test_extract_date_resolves_against_the_reference(text, expected):
    assert extract_date(text, REFERENCE) == expected


@pytest.mark.parametrize(
    "text",
    ["Ok.", "Please share the bank details.", "", "Which PO is this against?"],
)
def test_extract_date_returns_none_when_no_date_is_mentioned(text):
    assert extract_date(text, REFERENCE) is None


def test_invoice_reference_digits_are_not_read_as_a_date():
    assert extract_date("Regarding INV-2026-00027, noted.", REFERENCE) is None


def test_absolute_dates_win_over_relative_ones():
    text = "We said Friday earlier but it will be 20 September 2026."

    assert extract_date(text, REFERENCE) == date(2026, 9, 20)


# ---------------------------------------------------------------------------
# Currency and dispute reasons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Will pay ₹45,000", "INR"),
        ("Rs. 45,000 sent", "INR"),
        ("INR 45000", "INR"),
        ("we owe 500 rupees", "INR"),
        ("Paying USD 1,200", "USD"),
        ("$1,200 wired", "USD"),
        ("€900 due", "EUR"),
        ("Amount 45000", "INR"),
        ("", "INR"),
    ],
)
def test_normalize_currency(text, expected):
    assert normalize_currency(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The quantities billed do not match our GRN.", "quantity_mismatch"),
        ("The GST rate applied is wrong.", "tax_discrepancy"),
        ("You have charged freight twice.", "pricing_dispute"),
        ("Material arrived damaged.", "quality_issue"),
        ("Ok, noted.", None),
    ],
)
def test_extract_dispute_reason(text, expected):
    assert extract_dispute_reason(text) == expected


def test_extract_entities_populates_the_schema():
    entities = extract_entities("Will clear ₹45,000 by next Friday.", REFERENCE)

    assert entities.promised_amount == 45000.0
    assert entities.promised_date == date(2026, 9, 4)
    assert entities.currency == "INR"


# ---------------------------------------------------------------------------
# The extractor against the Phase 2 seed set
# ---------------------------------------------------------------------------


def test_extractor_matches_every_seed_amount(seed_examples):
    checked = 0
    for example in seed_examples:
        if not example.amount_checkable:
            continue
        checked += 1
        assert extract_amount(example.text) == example.expected_amount, example.text

    assert checked >= 10


def test_extractor_matches_every_seed_date(seed_examples):
    reference = datetime.combine(seed_examples[0].reference_date, datetime.min.time())

    checked = 0
    for example in seed_examples:
        if not example.date_checkable:
            continue
        checked += 1
        assert extract_date(example.text, reference) == example.expected_date, example.text

    assert checked >= 5


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_prompt_reaches_all_eight_intent_labels():
    prompt = build_classification_prompt("Will pay Friday.", {"invoice_id": "INV-1"})

    for label in IntentLabel:
        assert label.value in prompt


def test_prompt_includes_the_invoice_context():
    prompt = build_classification_prompt("ok", {"outstanding_amount": 45000})

    assert "outstanding_amount" in prompt
    assert "45000" in prompt


def test_strict_retry_prompt_demands_bare_json():
    strict = build_classification_prompt("ok", None, strict=True)

    assert "ONLY valid JSON" in strict
    assert len(strict) < len(build_classification_prompt("ok", None))


# ---------------------------------------------------------------------------
# The shared client's structured-output contract
# ---------------------------------------------------------------------------


class _ScriptedClient:
    """Concrete LLMClient whose raw responses are scripted."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def generate(self, prompt, system_prompt=None):
        self.prompts.append(prompt)
        return self.responses.pop(0)

    def is_available(self):
        return True


def _client_class():
    module = pytest.importorskip(
        "app.services.llm_client",
        reason="backend dependencies not installed",
    )
    return module


def test_generate_structured_parses_clean_json():
    module = _client_class()
    scripted = type("C", (_ScriptedClient, module.LLMClient), {})(
        ['{"intent": "PROMISE_TO_PAY", "confidence": 0.9}']
    )

    result = run(scripted.generate_structured("p", ReplyIntentLLMOutput))

    assert result.intent is IntentLabel.PROMISE_TO_PAY
    assert len(scripted.prompts) == 1


def test_generate_structured_survives_markdown_fences_and_prose():
    module = _client_class()
    scripted = type("C", (_ScriptedClient, module.LLMClient), {})(
        ['Sure!\n```json\n{"intent": "DISPUTE", "confidence": 0.8}\n```\nHope that helps.']
    )

    result = run(scripted.generate_structured("p", ReplyIntentLLMOutput))

    assert result.intent is IntentLabel.DISPUTE


def test_generate_structured_retries_once_then_succeeds():
    module = _client_class()
    scripted = type("C", (_ScriptedClient, module.LLMClient), {})(
        ["I cannot do that.", '{"intent": "OPT_OUT", "confidence": 0.99}']
    )

    result = run(scripted.generate_structured("p", ReplyIntentLLMOutput))

    assert result.intent is IntentLabel.OPT_OUT
    assert len(scripted.prompts) == 2


def test_generate_structured_raises_after_the_retry():
    module = _client_class()
    scripted = type("C", (_ScriptedClient, module.LLMClient), {})(
        ["not json", "still not json"]
    )

    with pytest.raises(module.StructuredOutputError):
        run(scripted.generate_structured("p", ReplyIntentLLMOutput))

    assert len(scripted.prompts) == 2


def test_generate_structured_rejects_an_intent_outside_the_taxonomy():
    module = _client_class()
    scripted = type("C", (_ScriptedClient, module.LLMClient), {})(
        ['{"intent": "MAYBE_PAY", "confidence": 0.9}'] * 2
    )

    with pytest.raises(module.StructuredOutputError):
        run(scripted.generate_structured("p", ReplyIntentLLMOutput))


# ---------------------------------------------------------------------------
# LLM output coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.9, 0.9), ("0.85", 0.85), (95, 0.95), ("88%", 0.88), (1.4, 1.0), (-2, 0.0)],
)
def test_confidence_is_coerced_rather_than_rejected(raw, expected):
    output = ReplyIntentLLMOutput(intent=IntentLabel.OTHER, confidence=raw)

    assert output.confidence == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Rs. 45,000", 45000.0), ("1.8L", 180000.0), (45000, 45000.0), ("none at all", None)],
)
def test_amount_strings_from_the_model_are_recovered(raw, expected):
    output = ReplyIntentLLMOutput(
        intent=IntentLabel.PROMISE_TO_PAY, confidence=0.9, promised_amount=raw
    )

    assert output.promised_amount == expected


# ---------------------------------------------------------------------------
# classify_reply_llm never raises
# ---------------------------------------------------------------------------


def test_classify_reply_llm_returns_a_prediction_on_success():
    stub = StubLLM(
        llm_output(
            IntentLabel.PROMISE_TO_PAY,
            promised_amount="Rs. 45,000",
            promised_date="next Friday",
        )
    )

    prediction = run(
        classify_reply_llm(
            "Will clear Rs. 45,000 by next Friday.",
            {"invoice_id": "INV-1"},
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            reference_dt=REFERENCE,
        )
    )

    assert isinstance(prediction, ReplyIntentPrediction)
    assert prediction.intent is IntentLabel.PROMISE_TO_PAY
    assert prediction.entities.promised_amount == 45000.0
    assert prediction.entities.promised_date == date(2026, 9, 4)
    assert prediction.fallback_used is False
    assert prediction.model_used == MODEL_USED
    assert prediction.model_version == PROMPT_VERSION


def test_malformed_llm_output_degrades_instead_of_crashing():
    stub = StubLLM(error=RuntimeError("No JSON object found in response"))

    prediction = run(
        classify_reply_llm(
            "will clear 1.8L by fri",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            reference_dt=REFERENCE,
        )
    )

    assert prediction.intent is IntentLabel.OTHER
    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.MALFORMED_OUTPUT


def test_llm_timeout_degrades_instead_of_hanging():
    stub = StubLLM(llm_output(IntentLabel.DISPUTE), delay=5.0)

    prediction = run(
        classify_reply_llm(
            "This invoice is wrong.",
            invoice_id="INV-1",
            reply_id="rpl-1",
            client=stub,
            reference_dt=REFERENCE,
            timeout_seconds=0.05,
        )
    )

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.TIMEOUT


def test_missing_provider_degrades_to_model_load_failed(monkeypatch):
    import src.ml.reply.llm_baseline as baseline

    monkeypatch.setattr(
        baseline,
        "get_structured_llm",
        lambda: (_ for _ in ()).throw(RuntimeError("No LLM provider configured.")),
    )

    prediction = run(classify_reply_llm("hello", invoice_id="INV-1", reply_id="rpl-1"))

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.MODEL_LOAD_FAILED


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_reply_text_degrades(text):
    prediction = run(classify_reply_llm(text, invoice_id="INV-1", reply_id="rpl-1"))

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.INVALID_OUTPUT


def test_unexpected_response_type_degrades():
    stub = StubLLM(output={"intent": "PROMISE_TO_PAY"})

    prediction = run(
        classify_reply_llm("Will pay Friday", invoice_id="INV-1", reply_id="rpl-1", client=stub)
    )

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.MALFORMED_OUTPUT


# ---------------------------------------------------------------------------
# understand_reply: the five intents the exit criteria name, end to end
# ---------------------------------------------------------------------------


def _understand(text, output, **kwargs):
    return run(
        understand_reply(
            text,
            kwargs.pop("invoice_id", "INV-1043"),
            kwargs.pop("reply_id", "rpl-1"),
            client=StubLLM(output),
            reference_dt=REFERENCE,
            **kwargs,
        )
    )


def test_promise_to_pay_becomes_a_structured_promise():
    prediction = _understand(
        "Will clear Rs. 45,000 by next Friday.",
        llm_output(IntentLabel.PROMISE_TO_PAY),
    )

    assert prediction.intent is IntentLabel.PROMISE_TO_PAY
    assert prediction.entities.promised_amount == 45000.0
    assert prediction.entities.promised_date == date(2026, 9, 4)
    assert prediction.entities.currency == "INR"
    assert prediction.fallback_used is False


def test_dispute_carries_a_reason_and_no_promise():
    prediction = _understand(
        "The quantities billed do not match our goods receipt note.",
        llm_output(IntentLabel.DISPUTE),
    )

    assert prediction.intent is IntentLabel.DISPUTE
    assert prediction.entities.dispute_reason == "quantity_mismatch"
    assert prediction.entities.promised_amount is None
    assert prediction.entities.promised_date is None


def test_opt_out_is_recognised():
    prediction = _understand(
        "Stop messaging me. Remove this number from your list.",
        llm_output(IntentLabel.OPT_OUT),
    )

    assert prediction.intent is IntentLabel.OPT_OUT


def test_negotiation_keeps_its_amount_and_date():
    prediction = _understand(
        "Will clear 1.8L by next Friday, waive the late fee please.",
        llm_output(IntentLabel.NEGOTIATION_REQUEST),
    )

    assert prediction.intent is IntentLabel.NEGOTIATION_REQUEST
    assert prediction.entities.promised_amount == 180000.0
    assert prediction.entities.promised_date == date(2026, 9, 4)


def test_already_paid_claim_is_recognised():
    prediction = _understand(
        "Payment was settled by NEFT on 22 August 2026.",
        llm_output(IntentLabel.ALREADY_PAID_CLAIM),
    )

    assert prediction.intent is IntentLabel.ALREADY_PAID_CLAIM
    assert prediction.entities.promised_date == date(2026, 8, 22)


# ---------------------------------------------------------------------------
# understand_reply: policy behaviour
# ---------------------------------------------------------------------------


def test_a_date_in_small_talk_is_not_recorded_as_a_promise():
    prediction = _understand(
        "Our office is closed for Diwali until next week.",
        llm_output(IntentLabel.OTHER),
    )

    assert prediction.intent is IntentLabel.OTHER
    assert prediction.entities.promised_date is None
    assert prediction.entities.promised_amount is None


def test_a_query_mentioning_a_date_does_not_create_a_promise():
    prediction = _understand(
        "Can you resend the invoice copy before Friday?",
        llm_output(IntentLabel.GENERAL_QUERY),
    )

    assert prediction.intent is IntentLabel.GENERAL_QUERY
    assert prediction.entities.promised_date is None


def test_only_entity_bearing_intents_can_hold_a_promise():
    assert IntentLabel.OPT_OUT not in ENTITY_BEARING_INTENTS
    assert IntentLabel.DISPUTE not in ENTITY_BEARING_INTENTS
    assert IntentLabel.PROMISE_TO_PAY in ENTITY_BEARING_INTENTS


@pytest.mark.parametrize(
    "text",
    [
        "Please unsubscribe us from these reminders.",
        "Do not contact me again on WhatsApp.",
        "Stop sending these messages.",
        "Remove my number from your list.",
        "Mujhe message mat bhejo.",
        "Kindly opt us out of this reminder channel.",
    ],
)
def test_opt_out_guard_recognises_explicit_stop_requests(text):
    assert looks_like_opt_out(text)


@pytest.mark.parametrize(
    "text",
    ["Will clear 45,000 on Friday.", "Please stop the delivery of the next order."],
)
def test_opt_out_guard_does_not_fire_on_ordinary_replies(text):
    assert not looks_like_opt_out(text)


def test_opt_out_overrides_a_wrong_classification():
    prediction = _understand(
        "This bill is wrong and do not contact me again.",
        llm_output(IntentLabel.DISPUTE, confidence=0.95),
    )

    assert prediction.intent is IntentLabel.OPT_OUT
    assert prediction.confidence == 1.0
    assert "opt-out guard" in (prediction.explanation or "")


def test_opt_out_is_binding_even_at_low_confidence():
    prediction = _understand(
        "Please unsubscribe us from these reminders.",
        llm_output(IntentLabel.OPT_OUT, confidence=0.15),
    )

    assert prediction.intent is IntentLabel.OPT_OUT
    assert prediction.fallback_used is False


def test_low_confidence_is_routed_to_human_review():
    prediction = _understand(
        "maybe next month, will see",
        llm_output(IntentLabel.PROMISE_TO_PAY, confidence=0.25),
    )

    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.LOW_CONFIDENCE
    assert "human review" in (prediction.explanation or "")


def test_a_classifier_that_raises_does_not_crash_the_pipeline():
    async def exploding_classifier(*args, **kwargs):
        raise ValueError("classifier exploded")

    set_primary_classifier(exploding_classifier)

    prediction = run(understand_reply("Will pay Friday.", "INV-1", "rpl-1"))

    assert prediction.intent is IntentLabel.OTHER
    assert prediction.fallback_used is True
    assert prediction.fallback.reason is FallbackReason.UNEXPECTED_ERROR


def test_primary_classifier_can_be_swapped_without_touching_the_service():
    """Phase 4 is a rebinding, not a rewrite."""

    async def trained_classifier(raw_text, invoice_context=None, **kwargs):
        return ReplyIntentPrediction(
            model_version="tfidf-svm-intent-v1",
            confidence=0.97,
            reply_id=kwargs.get("reply_id", ""),
            invoice_id=kwargs.get("invoice_id", ""),
            raw_text=raw_text,
            intent=IntentLabel.PROMISE_TO_PAY,
            model_used="tfidf-svm-intent-v1",
        )

    previous = set_primary_classifier(trained_classifier)

    prediction = run(
        understand_reply("Will clear 45,000 tomorrow.", "INV-1", "rpl-1", reference_dt=REFERENCE)
    )

    assert previous is not trained_classifier
    assert prediction.model_used == "tfidf-svm-intent-v1"
    assert prediction.entities.promised_amount == 45000.0
    assert prediction.entities.promised_date == date(2026, 9, 2)


def test_understand_reply_sync_refuses_to_run_inside_an_event_loop():
    async def inner():
        with pytest.raises(RuntimeError, match="running event loop"):
            understand_reply_sync("hello", "INV-1", "rpl-1")

    run(inner())


def test_understand_reply_sync_works_outside_an_event_loop():
    prediction = understand_reply_sync("hello", "INV-1", "rpl-1")

    assert isinstance(prediction, ReplyIntentPrediction)


# ---------------------------------------------------------------------------
# Every seed example produces a schema-conformant prediction
# ---------------------------------------------------------------------------


def test_every_seed_example_produces_a_valid_prediction(seed_examples):
    reference = datetime.combine(seed_examples[0].reference_date, datetime.min.time())

    for example in seed_examples:
        stub = StubLLM(llm_output(IntentLabel(example.intent)))
        prediction = run(
            understand_reply(
                example.text,
                example.invoice_id,
                example.reply_id,
                client=stub,
                reference_dt=reference,
            )
        )

        assert isinstance(prediction, ReplyIntentPrediction)
        assert prediction.raw_text == example.text
        assert 0.0 <= prediction.confidence <= 1.0
        assert prediction.scored_at.tzinfo is not None
        # Round-trips through the wire format unchanged.
        assert ReplyIntentPrediction.model_validate_json(prediction.model_dump_json())


def test_entities_are_recovered_for_every_promise_bearing_seed(seed_examples):
    reference = datetime.combine(seed_examples[0].reference_date, datetime.min.time())

    checked = 0
    for example in seed_examples:
        intent = IntentLabel(example.intent)
        if intent not in ENTITY_BEARING_INTENTS:
            continue

        stub = StubLLM(llm_output(intent))
        prediction = run(
            understand_reply(
                example.text,
                example.invoice_id,
                example.reply_id,
                client=stub,
                reference_dt=reference,
            )
        )

        if example.amount_checkable:
            checked += 1
            assert prediction.entities.promised_amount == example.expected_amount, example.text
        if example.date_checkable:
            checked += 1
            assert prediction.entities.promised_date == example.expected_date, example.text

    assert checked >= 15


def test_no_seed_example_ever_raises(seed_examples):
    """The whole point: strange text degrades, it does not crash."""

    for example in seed_examples:
        stub = StubLLM(error=RuntimeError("provider exploded"))
        prediction = run(
            understand_reply(
                example.text, example.invoice_id, example.reply_id, client=stub
            )
        )

        assert prediction.fallback_used is True
        assert prediction.fallback is not None
