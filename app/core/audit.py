"""The Decision Trace: an append-only, hash-chained record of every decision.

The README calls this an "immutable record of every decision and its reason".
An ordinary log table is not that -- it is a record nobody has edited *yet*. So
each entry commits to the one before it: ``entry_hash`` is a SHA-256 over the
entry's own content plus its predecessor's hash. Editing, reordering or
deleting an entry breaks every hash after it, and :meth:`DecisionLedger.verify`
reports the first position that no longer agrees.

Two design notes:

* **The ledger is pure Python.** It has no database dependency, so the batch
  demo and the unit tests get the same tamper-evidence the API does. The API
  layer mirrors entries into the ``decision_traces`` table; the hash chain is
  computed here, once, and persisted alongside.
* **A production system would reach for a library.** ``pyeventsourcing`` gives
  aggregates, snapshotting and replay properly. That is the documented next
  step in ``docs/architecture.md``; the hash chain here is the buildathon-sized
  version of the same guarantee, not a claim to have outdone it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DecisionOutcome
from src.ml.versioning import utc_now

#: The hash a first entry chains from. A fixed, well-known value rather than an
#: empty string, so "no predecessor" is stated rather than merely absent.
GENESIS_HASH = "0" * 64


class DecisionTraceEntry(BaseModel):
    """One immutable decision record."""

    model_config = ConfigDict(protected_namespaces=(), frozen=True)

    seq: int = Field(ge=0)
    invoice_id: str
    event: str = Field(min_length=1)
    outcome: DecisionOutcome
    reason: str = ""
    actor: str = "agent"
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=utc_now)
    prev_hash: str = GENESIS_HASH
    entry_hash: str = ""

    def content_digest(self) -> str:
        """Hash this entry's content together with its predecessor's hash.

        ``sort_keys`` and ``default=str`` make the serialisation canonical:
        the same entry always produces the same digest regardless of dict
        ordering or how a datetime happens to be rendered.
        """

        material = json.dumps(
            {
                "seq": self.seq,
                "invoice_id": self.invoice_id,
                "event": self.event,
                "outcome": self.outcome.value,
                "reason": self.reason,
                "actor": self.actor,
                "payload": self.payload,
                "recorded_at": self.recorded_at.isoformat(),
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class LedgerIntegrityError(RuntimeError):
    """Raised when the hash chain does not verify."""

    def __init__(self, message: str, seq: int) -> None:
        super().__init__(message)
        self.seq = seq


class DecisionLedger:
    """An append-only sequence of decision entries.

    Thread-safe because the FastAPI app and the scheduler can both append, and
    a torn read of ``_entries`` would assign two entries the same ``seq`` and
    silently fork the chain.
    """

    def __init__(self) -> None:
        self._entries: list[DecisionTraceEntry] = []
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[DecisionTraceEntry]:
        return iter(tuple(self._entries))

    @property
    def head_hash(self) -> str:
        """The hash of the most recent entry, or the genesis value."""

        return self._entries[-1].entry_hash if self._entries else GENESIS_HASH

    def append(
        self,
        *,
        invoice_id: str,
        event: str,
        outcome: DecisionOutcome,
        reason: str = "",
        actor: str = "agent",
        payload: dict[str, Any] | None = None,
        recorded_at: datetime | None = None,
    ) -> DecisionTraceEntry:
        """Add one entry and return it, sealed with its hash."""

        with self._lock:
            draft = DecisionTraceEntry(
                seq=len(self._entries),
                invoice_id=invoice_id,
                event=event,
                outcome=outcome,
                reason=reason,
                actor=actor,
                payload=payload or {},
                recorded_at=recorded_at or utc_now(),
                prev_hash=self.head_hash,
            )
            sealed = draft.model_copy(update={"entry_hash": draft.content_digest()})
            self._entries.append(sealed)
            return sealed

    def entries(self) -> tuple[DecisionTraceEntry, ...]:
        """Every entry, oldest first."""

        return tuple(self._entries)

    def for_invoice(self, invoice_id: str) -> tuple[DecisionTraceEntry, ...]:
        """This invoice's decision trail, oldest first."""

        return tuple(entry for entry in self._entries if entry.invoice_id == invoice_id)

    def verify(self) -> None:
        """Re-derive the chain, raising at the first entry that disagrees.

        Checks three things per entry: that ``seq`` is dense and ordered, that
        ``prev_hash`` matches the previous entry, and that ``entry_hash`` still
        matches the content. Any one of them failing means the record was
        altered after it was written.
        """

        expected_prev = GENESIS_HASH
        for position, entry in enumerate(self._entries):
            if entry.seq != position:
                raise LedgerIntegrityError(
                    f"Entry at position {position} claims seq {entry.seq}", position
                )
            if entry.prev_hash != expected_prev:
                raise LedgerIntegrityError(
                    f"Entry {entry.seq} does not chain to its predecessor", entry.seq
                )
            if entry.entry_hash != entry.content_digest():
                raise LedgerIntegrityError(
                    f"Entry {entry.seq} content does not match its hash", entry.seq
                )
            expected_prev = entry.entry_hash

    def is_valid(self) -> bool:
        """``verify`` as a boolean, for callers that only want the answer."""

        try:
            self.verify()
        except LedgerIntegrityError:
            return False
        return True


#: The ledger the agent writes to by default. Tests and the batch demo build
#: their own instance and pass it in explicitly, so they never contend with
#: this one or read each other's entries.
_default_ledger = DecisionLedger()


def get_ledger() -> DecisionLedger:
    """Return the process-wide default ledger."""

    return _default_ledger


def reset_ledger() -> DecisionLedger:
    """Replace the default ledger with an empty one and return it.

    Only for test setup and the demo script -- there is no legitimate reason to
    discard an audit trail at runtime.
    """

    global _default_ledger
    _default_ledger = DecisionLedger()
    return _default_ledger


def append_decision_trace(
    *,
    invoice_id: str,
    event: str,
    outcome: DecisionOutcome = DecisionOutcome.APPROVED,
    reason: str = "",
    actor: str = "agent",
    ledger: DecisionLedger | None = None,
    **payload: Any,
) -> DecisionTraceEntry:
    """Append one decision to ``ledger`` (default: the process-wide ledger).

    Extra keyword arguments are collected into the entry's payload, so callers
    can record whatever context the decision turned on without this signature
    growing a parameter per caller.
    """

    target = ledger if ledger is not None else get_ledger()
    return target.append(
        invoice_id=invoice_id,
        event=event,
        outcome=outcome,
        reason=reason,
        actor=actor,
        payload=payload,
    )
