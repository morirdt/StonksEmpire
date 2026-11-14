"""Running one job: take its lock, time it, and never let it kill the scheduler.

Jobs themselves are plain async functions in ``app/jobs/tasks/`` that take a
session factory and a clock and know nothing about APScheduler or locks. This
wrapper is what the scheduler actually calls.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.jobs.locks import JobLock, advisory_lock

__all__ = ["run_job"]

logger = structlog.stdlib.get_logger(__name__)


async def run_job(
    name: str,
    lock: JobLock,
    job: Callable[[], Awaitable[Any]],
    *,
    engine: AsyncEngine,
) -> Any | None:
    """Run ``job`` under its advisory lock. Returns its result, or ``None``.

    ``None`` means the run was skipped or failed, and the log line says which. A
    failure is logged and swallowed: an exception escaping into APScheduler
    would only be logged there, with less context, and the job must run again
    on its next tick either way.
    """
    log = logger.bind(job=name)
    async with advisory_lock(engine, lock) as acquired:
        if not acquired:
            log.info("job_skipped", reason="lock_held")
            return None

        started = time.perf_counter()
        try:
            result = await job()
        except Exception:
            log.exception("job_failed", duration_ms=_elapsed_ms(started))
            return None
        is_instance = dataclasses.is_dataclass(result) and not isinstance(result, type)
        summary = dataclasses.asdict(result) if is_instance else result
        log.info("job_finished", duration_ms=_elapsed_ms(started), result=summary)
        return result


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
