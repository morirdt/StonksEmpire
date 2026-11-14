"""Postgres advisory locks: one run of each job at a time, across every worker.

Every job run first takes ``pg_try_advisory_lock(key)`` on **a connection
dedicated to that run**. If it does not get the lock, it logs ``job_skipped``
and returns. That makes a second worker replica, or a run that overlaps the
next one, harmless.

- **Session-level, not ``pg_try_advisory_xact_lock``.** A job commits many times
  (once per symbol during ingest), and a transaction-scoped lock would be
  released at the first commit.
- **Released in ``finally``.** If the process dies instead, Postgres releases
  the lock when the connection drops. That property is why this is an
  advisory lock rather than a lock row, which a crash would leave behind.
- **Keys are fixed integers, listed once, below. Never ``hash(name)``.**
  ``hash()`` is salted per process, so two workers would compute different keys
  for the same job and both would run it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import IntEnum

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["JobLock", "advisory_lock"]

logger = structlog.stdlib.get_logger(__name__)


class JobLock(IntEnum):
    """One fixed key per job. A new job gets a new number; numbers are never reused."""

    POLL_QUOTES = 5_001
    INGEST_EOD = 5_002
    REFRESH_SYMBOLS = 5_003
    PRUNE_NOTIFICATIONS = 5_004


@asynccontextmanager
async def advisory_lock(engine: AsyncEngine, key: JobLock) -> AsyncIterator[bool]:
    """Try to take ``key`` on a dedicated connection. Yields whether it was taken.

    The connection is held, idle and outside any transaction, for the whole run,
    which is what keeps the lock. If unlocking fails, the connection is
    invalidated rather than returned to the pool: a pooled connection still
    holding the lock would block that job until the process restarted.
    """
    async with engine.connect() as connection:
        acquired = bool(
            (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": int(key)}
                )
            ).scalar_one()
        )
        # Ends the implicit transaction. The lock is session-level and survives.
        await connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": int(key)}
                    )
                    await connection.commit()
                except Exception:
                    logger.exception("advisory_unlock_failed", lock=key.name)
                    await connection.invalidate()
