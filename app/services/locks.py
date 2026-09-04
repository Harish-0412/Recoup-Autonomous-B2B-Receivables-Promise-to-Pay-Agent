"""Postgres advisory locks: one batch run at a time, across every replica.

The problem this solves is specific. A cron trigger and a retry can arrive
within milliseconds of each other, and two app replicas can both be reachable,
so two runs can start on the same book at the same time. Both would read the
same "contacts sent so far", both would decide the cap allowed one more, and
the customer gets two emails. The contact cap is only as good as the guarantee
that one process is counting at a time.

**Why an advisory lock rather than a table row.** A lock row needs a cleanup
story: a process that dies holding it leaves the lock set forever, and the
timeout that fixes that is a guess. A session-scoped advisory lock is released
by Postgres when the connection drops, whatever killed the process. Nothing to
reap, and no stale-lock timeout to tune.

**Why ``try`` rather than blocking.** A second trigger should be a no-op, not a
queue. Waiting for the lock would run the batch *again* the moment the first
finished, which is the double-send this exists to prevent, merely delayed.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

_MEM_LOCKS: dict[str, asyncio.Lock] = {}


def _get_mem_lock(name: str) -> asyncio.Lock:
    if name not in _MEM_LOCKS:
        _MEM_LOCKS[name] = asyncio.Lock()
    return _MEM_LOCKS[name]


def lock_key(name: str) -> int:
    """A stable 63-bit key for a named lock.

    ``pg_try_advisory_lock`` takes a bigint, so the name is hashed rather than
    assigned by hand -- hand-assigned integers collide the moment two people
    add a lock without checking each other's constants.
    """

    digest = hashlib.sha256(name.encode("utf-8")).digest()
    # Top bit cleared: Postgres bigint is signed, and a negative key works but
    # reads confusingly in pg_locks.
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


@asynccontextmanager
async def advisory_lock(session: AsyncSession, name: str) -> AsyncIterator[bool]:
    """Hold a named advisory lock for the block, if it is free.

    Yields True when the lock was acquired and False when someone else holds
    it. The caller decides what a miss means; for a batch run it means "another
    run is already going, do nothing".

    The lock is session-scoped, so it is tied to this connection rather than to
    the surrounding transaction: a commit part-way through a batch must not
    quietly drop it.
    """

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        mem_lock = _get_mem_lock(name)
        if mem_lock.locked():
            logger.info("Local lock is held elsewhere; skipping", lock=name)
            yield False
            return
        await mem_lock.acquire()
        logger.debug("Local advisory lock acquired", lock=name)
        try:
            yield True
        finally:
            if mem_lock.locked():
                mem_lock.release()
            logger.debug("Local advisory lock released", lock=name)
        return

    key = lock_key(name)
    result = await session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
    acquired = bool(result.scalar())

    if not acquired:
        logger.info("Advisory lock is held elsewhere; skipping", lock=name, key=key)
        yield False
        return

    logger.debug("Advisory lock acquired", lock=name, key=key)
    try:
        yield True
    finally:
        # Released explicitly on the happy path. If the process dies instead,
        # Postgres releases it when the connection closes -- which is the whole
        # reason for using an advisory lock rather than a row.
        await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        logger.debug("Advisory lock released", lock=name, key=key)
