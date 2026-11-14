"""The worker process: ``python -m app.jobs.worker``.

Wiring, and nothing but wiring. Every job is a plain function in
``app/jobs/tasks/`` and is tested by calling it directly. This module only says
when each one runs.

APScheduler 3.11 ``AsyncIOScheduler`` in ``America/New_York``, with the
in-memory job store. The schedule is defined here in code, so a persistent job
store would add nothing and could go stale. Every job is ``coalesce=True`` and
``max_instances=1``, with a ``misfire_grace_time`` shorter than its interval. A
backlog therefore collapses into one run rather than a burst.

Schedules are plain. Whether a run should do anything (market open, trading
day) is the first thing each job asks the calendar.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.market_calendar import MARKET_TZ
from app.core.redis import REDIS_UNAVAILABLE, close_redis
from app.db.session import SessionFactory, dispose_engine, engine
from app.jobs import heartbeat
from app.jobs.locks import JobLock
from app.jobs.runner import run_job
from app.jobs.tasks.market_data import ingest_eod, poll_quotes, refresh_symbols
from app.jobs.tasks.notifications import prune_notifications

__all__ = ["build_scheduler", "main"]

logger = structlog.stdlib.get_logger(__name__)


def _locked(
    name: str, lock: JobLock, job: Callable[[], Awaitable[Any]]
) -> Callable[[], Awaitable[Any]]:
    async def run() -> Any:
        return await run_job(name, lock, job, engine=engine)

    return run


async def _beat() -> None:
    try:
        await heartbeat.beat(utc_now)
    except REDIS_UNAVAILABLE as exc:
        # /ready will report the worker as down, which is the honest answer:
        # nobody can tell it is alive.
        logger.warning("heartbeat_failed", error=str(exc))


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings().worker
    scheduler = AsyncIOScheduler(timezone=MARKET_TZ)
    common: dict[str, Any] = {"coalesce": True, "max_instances": 1}

    scheduler.add_job(
        _locked(
            "poll_quotes",
            JobLock.POLL_QUOTES,
            lambda: poll_quotes(SessionFactory, utc_now),
        ),
        IntervalTrigger(seconds=settings.quote_poll_seconds),
        id="poll_quotes",
        name="poll_quotes",
        misfire_grace_time=settings.quote_poll_seconds // 2,
        **common,
    )
    # One more poll after the close, to pick up the closing prices that the
    # last in-session poll, a minute or so before 16:00, could not see. It
    # shares the lock, so it can never overlap a regular poll.
    scheduler.add_job(
        _locked(
            "poll_quotes_closing",
            JobLock.POLL_QUOTES,
            lambda: poll_quotes(SessionFactory, utc_now, closing_run=True),
        ),
        CronTrigger(day_of_week="mon-fri", hour=16, minute=5, timezone=MARKET_TZ),
        id="poll_quotes_closing",
        name="poll_quotes_closing",
        misfire_grace_time=600,
        **common,
    )
    scheduler.add_job(
        _locked("ingest_eod", JobLock.INGEST_EOD, lambda: ingest_eod(SessionFactory, utc_now)),
        CronTrigger(
            hour=settings.eod_ingest_time.hour,
            minute=settings.eod_ingest_time.minute,
            timezone=MARKET_TZ,
        ),
        id="ingest_eod",
        name="ingest_eod",
        misfire_grace_time=3600,
        **common,
    )
    scheduler.add_job(
        _locked(
            "refresh_symbols",
            JobLock.REFRESH_SYMBOLS,
            lambda: refresh_symbols(SessionFactory, utc_now),
        ),
        CronTrigger(day_of_week="sun", hour=6, minute=0, timezone=MARKET_TZ),
        id="refresh_symbols",
        name="refresh_symbols",
        misfire_grace_time=3600,
        **common,
    )
    scheduler.add_job(
        _locked(
            "prune_notifications",
            JobLock.PRUNE_NOTIFICATIONS,
            lambda: prune_notifications(SessionFactory, utc_now),
        ),
        CronTrigger(hour=3, minute=0, timezone=MARKET_TZ),
        id="prune_notifications",
        name="prune_notifications",
        misfire_grace_time=3600,
        **common,
    )
    scheduler.add_job(
        _beat,
        IntervalTrigger(seconds=15),
        id="heartbeat",
        name="heartbeat",
        misfire_grace_time=10,
        **common,
    )
    return scheduler


async def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.use_json_logs)
    # APScheduler logs every run at INFO, which with a 15 s heartbeat is a line
    # every few seconds. run_job's own job_finished line says more about each run.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    scheduler = build_scheduler()
    scheduler.start()
    # Beat immediately rather than 15 s from now, so a fresh worker is healthy
    # as soon as it is up.
    await _beat()
    logger.info(
        "worker_started",
        environment=settings.environment,
        jobs=[job.id for job in scheduler.get_jobs()],
    )

    try:
        await stop.wait()
    finally:
        scheduler.shutdown(wait=False)
        await close_redis()
        await dispose_engine()
        logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
