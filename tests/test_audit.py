"""Tests for the Decision Trace ledger.

The claim this module makes is "immutable record", and an immutability claim is
only worth what its tamper tests prove. Most of these tests edit the ledger
behind its own back and assert it notices.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.audit import (
    GENESIS_HASH,
    DecisionLedger,
    DecisionTraceEntry,
    LedgerIntegrityError,
    append_decision_trace,
    get_ledger,
    reset_ledger,
)
from app.models.enums import DecisionOutcome


def seed(ledger: DecisionLedger, count: int = 3) -> None:
    for index in range(count):
        ledger.append(
            invoice_id=f"INV-{index % 2}",
            event="scored",
            outcome=DecisionOutcome.APPROVED,
            reason=f"entry {index}",
        )


class TestAppend:
    def test_entries_are_sequenced_densely_from_zero(self, ledger):
        seed(ledger, 4)

        assert [entry.seq for entry in ledger] == [0, 1, 2, 3]

    def test_the_first_entry_chains_from_genesis(self, ledger):
        seed(ledger, 1)

        assert ledger.entries()[0].prev_hash == GENESIS_HASH

    def test_each_entry_chains_to_its_predecessor(self, ledger):
        seed(ledger, 3)
        entries = ledger.entries()

        for previous, current in zip(entries, entries[1:], strict=False):
            assert current.prev_hash == previous.entry_hash

    def test_entries_are_frozen(self, ledger):
        """Pydantic refuses the assignment outright, before any hash check."""

        seed(ledger, 1)

        with pytest.raises(ValidationError):
            ledger.entries()[0].reason = "rewritten"

    def test_payload_carries_arbitrary_context(self, ledger):
        append_decision_trace(
            invoice_id="INV-1",
            event="scored",
            ledger=ledger,
            p_recovery=0.42,
            tier="REMIND",
        )

        assert ledger.entries()[0].payload == {"p_recovery": 0.42, "tier": "REMIND"}


class TestTamperDetection:
    def test_editing_an_entry_breaks_verification(self, ledger):
        seed(ledger, 3)

        ledger._entries[1] = ledger._entries[1].model_copy(update={"reason": "edited"})

        with pytest.raises(LedgerIntegrityError) as excinfo:
            ledger.verify()
        assert excinfo.value.seq == 1

    def test_editing_a_payload_breaks_verification(self, ledger):
        """The payload is inside the digest, not merely alongside it."""

        seed(ledger, 2)
        ledger._entries[0] = ledger._entries[0].model_copy(update={"payload": {"amount": 999_999}})

        assert not ledger.is_valid()

    def test_deleting_an_entry_breaks_verification(self, ledger):
        seed(ledger, 4)

        del ledger._entries[2]

        with pytest.raises(LedgerIntegrityError):
            ledger.verify()

    def test_reordering_entries_breaks_verification(self, ledger):
        seed(ledger, 4)

        ledger._entries[1], ledger._entries[2] = ledger._entries[2], ledger._entries[1]

        with pytest.raises(LedgerIntegrityError):
            ledger.verify()

    def test_appending_a_forged_entry_breaks_verification(self, ledger):
        """A forged entry cannot be made to fit without the chain noticing."""

        seed(ledger, 2)
        forged = ledger._entries[-1].model_copy(
            update={"seq": 2, "reason": "payment received", "prev_hash": GENESIS_HASH}
        )
        ledger._entries.append(forged)

        with pytest.raises(LedgerIntegrityError):
            ledger.verify()

    def test_an_untouched_ledger_verifies(self, ledger):
        seed(ledger, 25)

        ledger.verify()
        assert ledger.is_valid()


class TestQuerying:
    def test_for_invoice_filters_and_preserves_order(self, ledger):
        seed(ledger, 6)

        trail = ledger.for_invoice("INV-0")

        assert all(entry.invoice_id == "INV-0" for entry in trail)
        assert [entry.seq for entry in trail] == sorted(entry.seq for entry in trail)

    def test_head_hash_tracks_the_latest_entry(self, ledger):
        assert ledger.head_hash == GENESIS_HASH

        seed(ledger, 2)
        assert ledger.head_hash == ledger.entries()[-1].entry_hash


class TestDefaultLedger:
    def test_reset_replaces_the_process_ledger(self):
        append_decision_trace(invoice_id="INV-1", event="scored")
        assert len(get_ledger()) >= 1

        fresh = reset_ledger()

        assert len(fresh) == 0
        assert get_ledger() is fresh

    def test_an_explicit_ledger_is_not_the_default_one(self, ledger):
        reset_ledger()
        append_decision_trace(invoice_id="INV-1", event="scored", ledger=ledger)

        assert len(ledger) == 1
        assert len(get_ledger()) == 0


def test_the_digest_is_stable_across_dict_ordering(ledger):
    """Canonical serialisation, so a re-serialised payload still verifies."""

    first = ledger.append(
        invoice_id="INV-1",
        event="scored",
        outcome=DecisionOutcome.APPROVED,
        payload={"b": 2, "a": 1},
    )
    reordered = first.model_copy(update={"payload": {"a": 1, "b": 2}})

    assert reordered.content_digest() == first.entry_hash


class TestDigestSurvivesPersistence:
    """Two ways the ledger used to accuse itself of tampering.

    Both were found by reading a trace back out of Postgres rather than out of
    memory, and both made ``chain_verified`` false for a ledger nobody had
    touched -- which is worse than no integrity check, because it trains people
    to ignore the one signal that is supposed to mean something.
    """

    def test_renumbering_seq_does_not_invalidate_the_hash(self):
        """``persist_ledger`` re-bases seq onto a global position.

        The digest must therefore not cover seq. Ordering is still committed
        to by prev_hash, which the chain test below exercises.
        """

        ledger = DecisionLedger()
        entry = ledger.append(invoice_id="INV-1", event="scored", outcome=DecisionOutcome.APPROVED)

        renumbered = entry.model_copy(update={"seq": entry.seq + 500})

        assert renumbered.content_digest() == entry.entry_hash

    def test_reordering_is_still_detected(self):
        """Dropping seq from the digest must not weaken tamper evidence."""

        ledger = DecisionLedger()
        for index in range(3):
            ledger.append(invoice_id="INV-1", event=f"e{index}", outcome=DecisionOutcome.APPROVED)
        assert ledger.is_valid()

        # Break the chain the way a reorder or deletion would.
        entries = list(ledger.entries())
        ledger._entries = [entries[0], entries[2]]  # noqa: SLF001

        assert ledger.is_valid() is False

    def test_editing_content_is_still_detected(self):
        ledger = DecisionLedger()
        entry = ledger.append(
            invoice_id="INV-1",
            event="scored",
            outcome=DecisionOutcome.APPROVED,
            reason="original",
        )

        tampered = entry.model_copy(update={"reason": "edited"})

        assert tampered.content_digest() != entry.entry_hash

    def test_the_digest_is_the_same_instant_in_any_timezone(self):
        """Postgres returns timestamptz in the session timezone.

        The same instant read back as +05:30 must hash identically to the
        +00:00 it was written as, or every row on a non-UTC server 'fails'.
        """

        from datetime import UTC, datetime, timedelta, timezone

        written_at = datetime(2026, 9, 3, 8, 30, tzinfo=UTC)
        read_back_at = written_at.astimezone(timezone(timedelta(hours=5, minutes=30)))
        assert written_at == read_back_at
        assert written_at.isoformat() != read_back_at.isoformat()

        base = {
            "seq": 0,
            "invoice_id": "INV-1",
            "event": "scored",
            "outcome": DecisionOutcome.APPROVED,
        }
        written = DecisionTraceEntry(**base, recorded_at=written_at)
        read_back = DecisionTraceEntry(**base, recorded_at=read_back_at)

        assert written.content_digest() == read_back.content_digest()

    def test_a_naive_timestamp_is_read_as_utc(self):
        from datetime import UTC, datetime

        base = {
            "seq": 0,
            "invoice_id": "INV-1",
            "event": "scored",
            "outcome": DecisionOutcome.APPROVED,
        }
        aware = DecisionTraceEntry(**base, recorded_at=datetime(2026, 9, 3, 8, 30, tzinfo=UTC))
        naive = DecisionTraceEntry(**base, recorded_at=datetime(2026, 9, 3, 8, 30))

        assert aware.content_digest() == naive.content_digest()
