from datetime import datetime

import pytest

from src.ml.reply.dataset import (
    ALL_TEMPLATES,
    CORE_TEMPLATES,
    EDGE_CASE_TEMPLATES,
    EdgeCaseKind,
    LabelledReply,
    SplitName,
    audit_corpus,
    build_corpus,
    corpus_texts_and_labels,
)
from src.ml.reply.entity_extraction import extract_amount, extract_date
from src.ml.reply.service import looks_like_opt_out
from src.ml.schemas import IntentLabel


@pytest.fixture(scope="module")
def corpus():
    return build_corpus(size=1200, seed=42)


# --- template coverage ------------------------------------------------------


def test_every_intent_has_at_least_fifteen_core_templates():
    counts: dict[IntentLabel, int] = {}
    for template in CORE_TEMPLATES:
        counts[template.intent] = counts.get(template.intent, 0) + 1

    assert set(counts) == set(IntentLabel)
    assert all(count >= 15 for count in counts.values()), counts


def test_template_ids_are_unique():
    ids = [template.template_id for template in ALL_TEMPLATES]

    assert len(ids) == len(set(ids))


def test_every_edge_case_kind_is_represented():
    kinds = {t.edge_case_kind for t in EDGE_CASE_TEMPLATES if t.edge_case_kind}

    assert kinds == set(EdgeCaseKind)


def test_every_dispute_template_declares_a_reason():
    for template in ALL_TEMPLATES:
        if template.intent is IntentLabel.DISPUTE:
            assert template.dispute_reason, template.template_id


def test_tone_coverage_includes_hinglish_and_formal_registers():
    tones = {template.tone for template in CORE_TEMPLATES}

    assert {"formal", "curt", "apologetic", "neutral", "hinglish"} <= tones


# --- corpus shape -----------------------------------------------------------


def test_corpus_is_the_requested_size_and_class_balanced(corpus):
    assert len(corpus.examples) == 1200

    counts = corpus.intent_counts()
    assert set(counts) == {label.value for label in IntentLabel}
    assert all(count == 150 for count in counts.values()), counts


def test_corpus_size_must_cover_every_template():
    with pytest.raises(ValueError):
        build_corpus(size=10, seed=1)


def test_unknown_split_strategy_is_rejected():
    with pytest.raises(ValueError):
        build_corpus(size=200, seed=1, split_strategy="stratified-ish")


def test_same_seed_reproduces_the_same_corpus():
    first = build_corpus(size=300, seed=5)
    second = build_corpus(size=300, seed=5)

    assert first.model_dump_json() == second.model_dump_json()


def test_different_seeds_produce_different_text():
    first = build_corpus(size=300, seed=5)
    second = build_corpus(size=300, seed=6)

    assert [e.text for e in first.examples] != [e.text for e in second.examples]


def test_every_example_carries_a_valid_intent_and_template(corpus):
    known_templates = {template.template_id for template in ALL_TEMPLATES}

    for example in corpus.examples:
        assert isinstance(example, LabelledReply)
        assert example.intent in set(IntentLabel)
        assert example.template_id in known_templates
        assert example.text.strip()
        assert "{" not in example.text


def test_edge_cases_are_present_but_a_minority(corpus):
    edge = [e for e in corpus.examples if e.is_edge_case]

    assert edge
    assert len(edge) / len(corpus.examples) < 0.25


# --- splitting --------------------------------------------------------------


def test_grouped_split_leaks_no_template_between_splits(corpus):
    by_template: dict[str, set[SplitName]] = {}
    for example in corpus.examples:
        by_template.setdefault(example.template_id, set()).add(example.split)

    leaked = {tid: splits for tid, splits in by_template.items() if len(splits) > 1}
    assert not leaked, f"templates spanning splits: {sorted(leaked)}"


def test_grouped_split_populates_all_three_splits(corpus):
    for split in SplitName:
        assert corpus.for_split(split), split


def test_grouped_split_keeps_every_intent_in_every_split(corpus):
    for split in SplitName:
        counts = corpus.intent_counts(split)
        assert all(count > 0 for count in counts.values()), (split, counts)


def test_grouped_split_is_roughly_seventy_fifteen_fifteen(corpus):
    total = len(corpus.examples)
    train = len(corpus.for_split(SplitName.TRAIN)) / total

    assert 0.6 <= train <= 0.8


def test_random_split_does_leak_templates_which_is_why_grouped_is_the_default():
    """The random strategy is kept for comparison; it is not the honest one."""

    random_corpus = build_corpus(size=1200, seed=42, split_strategy="random")

    by_template: dict[str, set[SplitName]] = {}
    for example in random_corpus.examples:
        by_template.setdefault(example.template_id, set()).add(example.split)

    spanning = [tid for tid, splits in by_template.items() if len(splits) > 1]
    assert spanning, "random splitting is expected to put a template in several splits"


def test_random_split_is_stratified_by_intent():
    random_corpus = build_corpus(size=1200, seed=42, split_strategy="random")

    for split in SplitName:
        counts = random_corpus.intent_counts(split)
        assert all(count > 0 for count in counts.values()), (split, counts)


# --- ground truth is correct by construction --------------------------------


def test_declared_amounts_are_recoverable_from_the_text(corpus):
    checked = 0
    for example in corpus.examples:
        if example.expected_amount is None:
            continue
        checked += 1
        assert extract_amount(example.text) == example.expected_amount, example.text

    assert checked > 100


def test_declared_dates_are_recoverable_from_the_text(corpus):
    checked = 0
    for example in corpus.examples:
        if example.expected_date is None:
            continue
        checked += 1
        reference = datetime.combine(example.reference_date, datetime.min.time())
        assert extract_date(example.text, reference) == example.expected_date, example.text

    assert checked > 100


def test_opt_out_examples_are_labelled_opt_out(corpus):
    """The binding guard and the corpus must not disagree about what opt-out is."""

    for example in corpus.examples:
        if looks_like_opt_out(example.text):
            assert example.intent is IntentLabel.OPT_OUT, example.text


# --- audit ------------------------------------------------------------------


def test_full_corpus_audit_is_clean(corpus):
    report, _ = audit_corpus(corpus, sample_fraction=1.0)

    assert report.clean, [f.problem for f in report.findings[:5]]
    assert report.sampled == len(corpus.examples)


def test_audit_samples_roughly_ten_percent(corpus):
    report, sample = audit_corpus(corpus, sample_fraction=0.10)

    assert report.sampled == len(sample) == 120
    assert len({e.reply_id for e in sample}) == len(sample)


def test_audit_reports_opt_out_guard_misses_without_failing(corpus):
    """Typo'd opt-outs are data the classifier must handle, not corpus faults."""

    report, _ = audit_corpus(corpus, sample_fraction=1.0)

    assert report.opt_out_guard_misses >= 0
    assert report.clean


def test_audit_catches_a_deliberately_mislabelled_example(corpus):
    broken = corpus.model_copy(deep=True)
    broken.examples[0].intent = IntentLabel.OPT_OUT
    broken.examples[0].text = "We will pay the invoice on Friday."
    broken.examples[0].expected_amount = None
    broken.examples[0].expected_date = None

    report, _ = audit_corpus(broken, sample_fraction=1.0)

    assert not report.clean
    problems = {f.problem for f in report.findings}
    assert any("opt-out cue" in problem for problem in problems), problems


def test_audit_catches_an_unrecoverable_declared_amount(corpus):
    broken = corpus.model_copy(deep=True)
    target = next(e for e in broken.examples if e.expected_amount is not None)
    target.expected_amount = 999999.0

    report, _ = audit_corpus(broken, sample_fraction=1.0)

    assert any("amount is not recoverable" in f.problem for f in report.findings)


def test_audit_rejects_an_invalid_sample_fraction(corpus):
    with pytest.raises(ValueError):
        audit_corpus(corpus, sample_fraction=0.0)


# --- sklearn hand-off -------------------------------------------------------


def test_corpus_texts_and_labels_line_up(corpus):
    train = corpus.for_split(SplitName.TRAIN)
    texts, labels = corpus_texts_and_labels(train)

    assert len(texts) == len(labels) == len(train)
    assert labels[0] == train[0].intent.value
    assert set(labels) <= {label.value for label in IntentLabel}
