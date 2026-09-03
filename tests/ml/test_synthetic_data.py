import json
from datetime import date, timedelta

import pytest

from src.data.synthetic_generator import (
    ARCHETYPE_PROFILES,
    HIDDEN_FIELDS,
    REPLY_TEMPLATES,
    CustomerArchetype,
    _inr_group,
    _render_amount,
    generate_batch,
    generate_customers,
    generate_invoices,
    make_rng,
    simulate_outcomes,
)
from src.ml.data.export import (
    FORBIDDEN_IN_TRAINING,
    LABEL_COLUMNS,
    TRAINING_FRAME_COLUMNS,
    LeakageError,
    export_batch,
    to_customers_frame,
    to_labels_frame,
    to_training_frame,
)
from src.ml.schemas import IntentLabel


@pytest.fixture(scope="module")
def batch():
    return generate_batch(batch_size=600, seed=42)


@pytest.fixture(scope="module")
def large_batch():
    """Bigger sample, for the statistical checks that need one."""

    return generate_batch(batch_size=2500, customer_count=500, seed=7)


# --- the generator produces all four kinds of record ------------------------


def test_batch_contains_customers_invoices_outcomes_and_replies(batch):
    assert len(batch.customers) == 150
    assert len(batch.invoices) == 600
    assert len(batch.reply_seed_examples) == len(REPLY_TEMPLATES)
    assert all(invoice.recovered is not None for invoice in batch.invoices)


def test_every_invoice_belongs_to_a_generated_customer(batch):
    known = {customer.customer_id for customer in batch.customers}

    assert {invoice.customer_id for invoice in batch.invoices} <= known


def test_all_five_archetypes_appear(batch):
    counts = batch.archetype_counts()

    assert set(counts) == {archetype.value for archetype in CustomerArchetype}
    assert all(count > 0 for count in counts.values())


# --- reproducibility --------------------------------------------------------


def test_same_seed_reproduces_the_same_batch():
    first = generate_batch(batch_size=200, seed=99)
    second = generate_batch(batch_size=200, seed=99)

    assert first.model_dump_json() == second.model_dump_json()


def test_different_seeds_produce_different_batches():
    first = generate_batch(batch_size=200, seed=1)
    second = generate_batch(batch_size=200, seed=2)

    assert first.model_dump_json() != second.model_dump_json()


def test_same_seed_writes_byte_identical_files(tmp_path):
    exports = []
    for name in ("run_a", "run_b"):
        run = generate_batch(batch_size=250, seed=123)
        out_dir = tmp_path / name
        export_batch(
            run.customers,
            run.invoices,
            out_dir,
            seed=run.seed,
            horizon_days=run.horizon_days,
            as_of=run.as_of,
            reply_seed_examples=run.reply_seed_examples,
        )
        exports.append(out_dir)

    first, second = exports
    data_files = [
        "customers.csv",
        "invoices.csv",
        "training_frame.csv",
        "labels.csv",
        "reply_seed_examples.json",
    ]
    for name in data_files:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name

    # The manifest carries a hash of every file, so "same seed, same data" is a
    # checkable claim rather than an assertion.
    first_manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    second_manifest = json.loads((second / "manifest.json").read_text(encoding="utf-8"))
    assert first_manifest["content_sha256"] == second_manifest["content_sha256"]


# --- the archetype never escapes -------------------------------------------


def test_archetype_is_dropped_by_model_dump(batch):
    dumped = batch.customers[0].model_dump()

    assert "archetype" not in dumped
    # ...but is still readable internally, which is what the simulator needs.
    assert batch.customers[0].archetype in set(CustomerArchetype)


def test_hidden_fields_are_absent_from_the_training_frame(batch):
    frame = to_training_frame(batch.customers, batch.invoices)

    for hidden in HIDDEN_FIELDS:
        assert hidden not in frame.columns
        assert not any(column.endswith(hidden) for column in frame.columns)


def test_labels_are_absent_from_the_training_frame(batch):
    frame = to_training_frame(batch.customers, batch.invoices)

    for label in LABEL_COLUMNS:
        assert label not in frame.columns
    assert list(frame.columns) == list(TRAINING_FRAME_COLUMNS)


def test_customers_frame_has_no_archetype_column(batch):
    frame = to_customers_frame(batch.customers)

    assert "archetype" not in frame.columns


def test_exported_csv_headers_carry_no_hidden_or_label_columns(tmp_path, batch):
    export_batch(
        batch.customers,
        batch.invoices,
        tmp_path,
        seed=batch.seed,
        horizon_days=batch.horizon_days,
        as_of=batch.as_of,
        reply_seed_examples=batch.reply_seed_examples,
    )

    header = (tmp_path / "training_frame.csv").read_text(encoding="utf-8").splitlines()[0]
    columns = header.split(",")

    assert set(columns).isdisjoint(FORBIDDEN_IN_TRAINING)
    assert "archetype" not in (tmp_path / "customers.csv").read_text(encoding="utf-8")


def test_leakage_guard_actually_fires(batch):
    frame = to_training_frame(batch.customers, batch.invoices)
    frame["recovered"] = [invoice.recovered for invoice in batch.invoices]

    from src.ml.data.export import _assert_no_leakage

    with pytest.raises(LeakageError):
        _assert_no_leakage(frame, context="test frame")


# --- outcome simulation is coherent ----------------------------------------


def test_recovered_invoices_land_inside_the_horizon(batch):
    for invoice in batch.invoices:
        if invoice.recovered:
            assert invoice.recovered_date is not None
            delta = (invoice.recovered_date - invoice.flagged_date).days
            assert 1 <= delta <= batch.horizon_days
        else:
            assert invoice.recovered_date is None


def test_invoice_dates_are_internally_consistent(batch):
    for invoice in batch.invoices:
        assert invoice.issue_date < invoice.due_date <= invoice.flagged_date
        assert (invoice.due_date - invoice.issue_date).days == invoice.payment_terms_days
        assert (invoice.flagged_date - invoice.due_date).days == invoice.days_overdue_at_flag
        assert invoice.days_since_last_contact <= invoice.days_overdue_at_flag


def test_prior_promise_fields_agree(batch):
    for invoice in batch.invoices:
        if invoice.has_prior_promise:
            assert invoice.prior_promise_kept is not None
        else:
            assert invoice.prior_promise_kept is None


def test_horizon_is_respected(batch):
    short = generate_batch(batch_size=300, seed=5, horizon_days=7)

    for invoice in short.invoices:
        if invoice.recovered:
            assert (invoice.recovered_date - invoice.flagged_date).days <= 7


def test_a_shorter_horizon_recovers_no_more_than_a_longer_one():
    short = generate_batch(batch_size=800, seed=11, horizon_days=7)
    long = generate_batch(batch_size=800, seed=11, horizon_days=60)

    short_rate = sum(1 for inv in short.invoices if inv.recovered) / len(short.invoices)
    long_rate = sum(1 for inv in long.invoices if inv.recovered) / len(long.invoices)

    assert short_rate < long_rate


# --- statistical sanity: the archetypes actually behave differently ---------


def _recovery_rate_by_archetype(batch) -> dict[str, float]:
    lookup = {customer.customer_id: customer for customer in batch.customers}
    totals: dict[str, list[int]] = {}
    for invoice in batch.invoices:
        key = lookup[invoice.customer_id].archetype.value
        totals.setdefault(key, []).append(1 if invoice.recovered else 0)
    return {key: sum(values) / len(values) for key, values in totals.items()}


def test_reliable_customers_recover_far_more_often_than_erratic_ones(large_batch):
    rates = _recovery_rate_by_archetype(large_batch)

    assert rates["RELIABLE"] > rates["ERRATIC"] + 0.25


def test_late_but_pays_sits_between_reliable_and_erratic(large_batch):
    rates = _recovery_rate_by_archetype(large_batch)

    assert rates["ERRATIC"] < rates["LATE_BUT_PAYS"] < rates["RELIABLE"]


def test_risk_escalating_customers_look_worse_recently_than_historically(large_batch):
    escalating = [
        customer
        for customer in large_batch.customers
        if customer.archetype is CustomerArchetype.RISK_ESCALATING
    ]
    assert escalating

    recent = sum(c.on_time_ratio_90d for c in escalating) / len(escalating)
    all_time = sum(c.on_time_ratio_all_time for c in escalating) / len(escalating)

    # This gap is the whole point of the archetype: it is what drift detection
    # is supposed to notice.
    assert recent < all_time - 0.20


def test_new_customers_have_short_histories(large_batch):
    new_customers = [
        customer
        for customer in large_batch.customers
        if customer.archetype is CustomerArchetype.NEW_UNKNOWN
    ]
    assert new_customers

    low, high = ARCHETYPE_PROFILES[CustomerArchetype.NEW_UNKNOWN].tenure_months_range
    assert all(low <= customer.tenure_months <= high for customer in new_customers)


def test_outcomes_are_not_a_deterministic_function_of_the_features(large_batch):
    """Both outcomes must occur within every archetype.

    A simulator with a clean decision boundary produces models that look
    perfect and prove nothing.
    """

    lookup = {customer.customer_id: customer for customer in large_batch.customers}
    seen: dict[str, set[bool]] = {}
    for invoice in large_batch.invoices:
        key = lookup[invoice.customer_id].archetype.value
        seen.setdefault(key, set()).add(bool(invoice.recovered))

    for archetype, outcomes in seen.items():
        assert outcomes == {True, False}, f"{archetype} has only {outcomes}"


# --- export mechanics -------------------------------------------------------


def test_manifest_counts_match_the_generated_batch(tmp_path, batch):
    manifest_path = export_batch(
        batch.customers,
        batch.invoices,
        tmp_path,
        seed=batch.seed,
        horizon_days=batch.horizon_days,
        as_of=batch.as_of,
        reply_seed_examples=batch.reply_seed_examples,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["seed"] == batch.seed
    assert manifest["horizon_days"] == batch.horizon_days
    assert manifest["row_counts"]["customers"] == len(batch.customers)
    assert manifest["row_counts"]["invoices"] == len(batch.invoices)
    assert manifest["row_counts"]["training_frame"] == len(batch.invoices)
    assert manifest["row_counts"]["labels"] == len(batch.invoices)
    assert manifest["row_counts"]["reply_seed_examples"] == len(batch.reply_seed_examples)
    assert manifest["archetype_counts"] == batch.archetype_counts()
    assert sum(manifest["archetype_counts"].values()) == len(batch.customers)


def test_labels_frame_joins_one_to_one_with_the_training_frame(batch):
    training = to_training_frame(batch.customers, batch.invoices)
    labels = to_labels_frame(batch.invoices, horizon_days=batch.horizon_days)

    assert list(training["invoice_id"]) == list(labels["invoice_id"])
    assert labels["invoice_id"].is_unique


def test_export_creates_a_missing_output_directory(tmp_path, batch):
    target = tmp_path / "nested" / "run"

    export_batch(
        batch.customers,
        batch.invoices,
        target,
        seed=batch.seed,
        reply_seed_examples=batch.reply_seed_examples,
    )

    assert (target / "manifest.json").exists()


def test_as_of_is_the_flag_date(batch):
    frame = to_training_frame(batch.customers, batch.invoices)

    assert list(frame["as_of"]) == [invoice.flagged_date for invoice in batch.invoices]


# --- reply seed examples ----------------------------------------------------


def test_seed_examples_cover_all_eight_intents(batch):
    per_intent: dict[str, int] = {}
    for example in batch.reply_seed_examples:
        per_intent[example.intent] = per_intent.get(example.intent, 0) + 1

    assert set(per_intent) == {label.value for label in IntentLabel}
    assert all(count == 5 for count in per_intent.values())


def test_seed_example_intents_are_valid_labels(batch):
    for example in batch.reply_seed_examples:
        assert IntentLabel(example.intent)


def test_seed_examples_declare_the_entities_they_contain(batch):
    for example in batch.reply_seed_examples:
        if example.amount_checkable:
            assert example.expected_amount is not None
        else:
            assert example.expected_amount is None

        if example.date_checkable:
            assert example.expected_date is not None
        else:
            assert example.expected_date is None


def test_seed_example_text_has_no_unfilled_placeholders(batch):
    for example in batch.reply_seed_examples:
        assert "{" not in example.text
        assert "}" not in example.text
        assert "  " not in example.text


def test_already_paid_claims_reference_a_past_date(batch):
    for example in batch.reply_seed_examples:
        if example.intent == IntentLabel.ALREADY_PAID_CLAIM.value and example.expected_date:
            assert example.expected_date < example.reference_date


def test_promise_dates_are_in_the_future(batch):
    for example in batch.reply_seed_examples:
        if example.intent == IntentLabel.PROMISE_TO_PAY.value and example.expected_date:
            assert example.expected_date > example.reference_date


# --- rendering helpers ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(500, "500"), (5_000, "5,000"), (180_000, "1,80,000"), (12_500_000, "1,25,00,000")],
)
def test_indian_digit_grouping(value, expected):
    assert _inr_group(value) == expected


def test_round_lakh_amounts_render_without_losing_digits():
    """A naive rstrip('0') turns 10.00 lakh into '1L'. It must not."""

    rng = make_rng(3)
    rendered = {_render_amount(rng, 1_000_000) for _ in range(60)}

    assert not any(text in {"1L", "₹1 lakh"} for text in rendered)
    assert any("10" in text for text in rendered)


# --- generator building blocks used directly --------------------------------


def test_simulate_outcomes_rejects_an_unknown_customer():
    rng = make_rng(0)
    customers = generate_customers(5, rng)
    invoices = generate_invoices(customers, rng, batch_size=5, as_of=date(2026, 9, 1))
    invoices[0].customer_id = "CUS-9999"

    with pytest.raises(KeyError):
        simulate_outcomes(invoices, customers, rng)


def test_generate_customers_rejects_a_non_positive_count():
    with pytest.raises(ValueError):
        generate_customers(0, make_rng(0))


def test_generate_invoices_rejects_an_empty_customer_list():
    with pytest.raises(ValueError):
        generate_invoices([], make_rng(0), batch_size=5, as_of=date(2026, 9, 1))


def test_scoring_dates_sit_at_or_just_before_as_of():
    as_of = date(2026, 9, 1)
    rng = make_rng(4)
    customers = generate_customers(20, rng)
    invoices = generate_invoices(customers, rng, batch_size=100, as_of=as_of)

    for invoice in invoices:
        assert as_of - timedelta(days=3) <= invoice.flagged_date <= as_of
