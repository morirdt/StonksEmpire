"""The worker's jobs, called directly at frozen instants.

Jobs take a session factory and a clock and know nothing about the scheduler,
so these tests are the jobs' real behaviour: no APScheduler, no sleeping.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date

import pytest
import structlog
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.jobs import heartbeat
from app.jobs.locks import JobLock, advisory_lock
from app.jobs.runner import run_job
from app.jobs.tasks.market_data import ingest_eod, poll_quotes, refresh_symbols
from app.models.market_data import DailyBar, LatestQuote
from app.models.symbol import Symbol
from app.services.market_data_service import MarketDataService
from tests.integration.conftest import SEED_TICKERS, FrozenClock
from tests.integration.providers import UNIVERSE, DownProvider, Published, RecordingProvider

pytestmark = pytest.mark.integration

type Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def lock_engine() -> AsyncGenerator[AsyncEngine]:
    """A real engine, outside the test transaction, as the worker would hold."""
    engine = create_async_engine(get_settings().db.url, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _watch(client: AsyncClient, *tickers: str) -> None:
    created = await client.post("/api/v1/watchlists", json={"name": "Watching"})
    assert created.status_code == 201, created.text
    for ticker in tickers:
        added = await client.post(
            f"/api/v1/watchlists/{created.json()['id']}/items", json={"ticker": ticker}
        )
        assert added.status_code == 201, added.text


async def _symbol(session: AsyncSession, ticker: str) -> Symbol:
    return (await session.execute(select(Symbol).where(Symbol.ticker == ticker))).scalar_one()


# ----------------------------------------------------------------- advisory locks


async def test_a_held_lock_skips_the_run_and_writes_nothing(lock_engine: AsyncEngine) -> None:
    calls: list[str] = []

    async def job() -> str:
        calls.append("ran")
        return "done"

    async with lock_engine.connect() as other_worker:
        await other_worker.execute(
            text("SELECT pg_advisory_lock(:k)"), {"k": int(JobLock.INGEST_EOD)}
        )
        with structlog.testing.capture_logs() as logs:
            result = await run_job("ingest_eod", JobLock.INGEST_EOD, job, engine=lock_engine)

    assert result is None
    assert calls == []
    assert any(e["event"] == "job_skipped" for e in logs)

    # The other connection is closed, which releases its lock: the next run goes ahead.
    assert await run_job("ingest_eod", JobLock.INGEST_EOD, job, engine=lock_engine) == "done"
    assert calls == ["ran"]


async def test_the_lock_is_released_after_a_failed_run(lock_engine: AsyncEngine) -> None:
    async def boom() -> None:
        raise RuntimeError("job blew up")

    with structlog.testing.capture_logs() as logs:
        assert await run_job("poll_quotes", JobLock.POLL_QUOTES, boom, engine=lock_engine) is None

    assert any(e["event"] == "job_failed" for e in logs)
    async with advisory_lock(lock_engine, JobLock.POLL_QUOTES) as acquired:
        assert acquired, "a failed run must not leave its lock behind"


async def test_two_runs_of_one_job_cannot_overlap(lock_engine: AsyncEngine) -> None:
    async with advisory_lock(lock_engine, JobLock.POLL_QUOTES) as first:
        async with advisory_lock(lock_engine, JobLock.POLL_QUOTES) as second:
            assert first and not second
        async with advisory_lock(lock_engine, JobLock.INGEST_EOD) as different:
            assert different, "each job has its own key"


def test_lock_keys_are_fixed_and_distinct() -> None:
    """Never hash(name): it is salted per process, so two workers would disagree."""
    keys = [int(lock) for lock in JobLock]
    assert len(set(keys)) == len(keys)
    assert JobLock.POLL_QUOTES == 5_001


# -------------------------------------------------------------------- poll quotes


async def test_poll_quotes_does_nothing_while_the_market_is_closed(
    session_factory: Factory, clock: FrozenClock, seeded_symbols: list[Symbol]
) -> None:
    clock.set_et(2026, 11, 14, 11, 0)  # Saturday
    published = Published()

    result = await poll_quotes(
        session_factory, clock, provider=RecordingProvider(clock), publish=published
    )

    assert result.skipped == "market_closed"
    assert published.events == []


async def test_poll_quotes_refreshes_only_the_hot_set(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    await _watch(auth_client, "AAPL")
    published = Published()

    result = await poll_quotes(
        session_factory, clock, provider=RecordingProvider(clock), publish=published
    )

    assert (result.hot_set, result.refreshed) == (1, 1)
    quotes = (await db_session.execute(select(LatestQuote))).scalars().all()
    assert [q.symbol_id for q in quotes] == [(await _symbol(db_session, "AAPL")).id]
    assert quotes[0].fetched_at == clock.at

    (event,) = published.events
    assert event["type"] == "quotes_updated"
    assert event["tickers"] == ["AAPL"]
    assert "price" not in event, "a push is a hint; prices travel over REST"


async def test_poll_quotes_with_nobody_watching_fetches_nothing(
    session_factory: Factory, clock: FrozenClock, seeded_symbols: list[Symbol]
) -> None:
    published = Published()

    result = await poll_quotes(
        session_factory, clock, provider=RecordingProvider(clock), publish=published
    )

    assert result.hot_set == 0
    assert published.events == []


async def test_a_provider_outage_refreshes_nothing_and_pushes_nothing(
    session_factory: Factory,
    db_session: AsyncSession,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    await _watch(auth_client, "AAPL")
    published = Published()

    result = await poll_quotes(
        session_factory, clock, provider=DownProvider(UNIVERSE, clock=clock), publish=published
    )

    assert result.refreshed == 0
    assert published.events == []
    assert (await db_session.execute(select(LatestQuote))).first() is None


async def test_the_closing_run_goes_ahead_after_the_close_on_a_trading_day(
    session_factory: Factory,
    auth_client: AsyncClient,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    await _watch(auth_client, "AAPL")
    clock.set_et(2026, 11, 18, 16, 5)
    provider = RecordingProvider(clock)

    regular = await poll_quotes(session_factory, clock, provider=provider, publish=Published())
    closing = await poll_quotes(
        session_factory, clock, closing_run=True, provider=provider, publish=Published()
    )

    assert regular.skipped == "market_closed"
    assert closing.refreshed == 1


async def test_the_closing_run_skips_a_holiday(
    session_factory: Factory, clock: FrozenClock, seeded_symbols: list[Symbol]
) -> None:
    clock.set_et(2026, 11, 26, 16, 5)  # Thanksgiving

    result = await poll_quotes(
        session_factory, clock, closing_run=True, provider=RecordingProvider(clock)
    )

    assert result.skipped == "not_a_trading_day"


# --------------------------------------------------------------------- ingest eod


async def _store_bars(
    session: AsyncSession, clock: FrozenClock, ticker: str, start: date, end: date
) -> None:
    service = MarketDataService(session, provider=RecordingProvider(clock))
    await service.ingest_range(await _symbol(session, ticker), start, end)


async def test_ingest_fetches_only_the_gap_and_skips_what_needs_a_backfill(
    session_factory: Factory,
    db_session: AsyncSession,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    clock.set_et(2026, 11, 18, 17, 30)
    # AAPL is two sessions behind. MSFT stopped well over 30 days ago. SPY has nothing.
    await _store_bars(db_session, clock, "AAPL", date(2026, 9, 1), date(2026, 11, 16))
    await _store_bars(db_session, clock, "MSFT", date(2026, 8, 1), date(2026, 10, 1))
    provider = RecordingProvider(clock)

    result = await ingest_eod(session_factory, clock, provider=provider)

    assert provider.bar_requests == [("AAPL", date(2026, 11, 17), date(2026, 11, 18))]
    assert result.session == date(2026, 11, 18)
    assert (result.ingested, result.bars) == (1, 2)
    assert result.too_far_behind == ["MSFT"]
    assert result.no_history == ["SPY"]

    aapl = await _symbol(db_session, "AAPL")
    latest = await db_session.scalar(
        select(DailyBar.trade_date)
        .where(DailyBar.symbol_id == aapl.id)
        .order_by(DailyBar.trade_date.desc())
        .limit(1)
    )
    assert latest == date(2026, 11, 18)


async def test_a_second_ingest_the_same_night_changes_nothing(
    session_factory: Factory,
    db_session: AsyncSession,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    clock.set_et(2026, 11, 18, 17, 30)
    await _store_bars(db_session, clock, "AAPL", date(2026, 9, 1), date(2026, 11, 16))
    await ingest_eod(session_factory, clock, provider=RecordingProvider(clock))
    provider = RecordingProvider(clock)

    again = await ingest_eod(session_factory, clock, provider=provider)

    assert provider.bar_requests == []
    assert (again.ingested, again.up_to_date, again.bars) == (0, 1, 0)


async def test_ingest_before_the_close_targets_the_previous_session(
    session_factory: Factory,
    db_session: AsyncSession,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    """A run that fires early must not store a bar for a session still in progress."""
    clock.set_et(2026, 11, 18, 15, 0)
    await _store_bars(db_session, clock, "AAPL", date(2026, 9, 1), date(2026, 11, 16))

    result = await ingest_eod(session_factory, clock, provider=RecordingProvider(clock))

    assert result.session == date(2026, 11, 17)


async def test_ingest_does_not_run_on_a_holiday(
    session_factory: Factory, clock: FrozenClock, seeded_symbols: list[Symbol]
) -> None:
    clock.set_et(2026, 11, 26, 17, 30)

    result = await ingest_eod(session_factory, clock, provider=RecordingProvider(clock))

    assert result.skipped == "not_a_trading_day"


# ---------------------------------------------------------------- refresh symbols


async def test_refresh_deactivates_a_vanished_ticker_and_never_deletes_it(
    session_factory: Factory,
    db_session: AsyncSession,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    without_msft = [s for s in UNIVERSE if s.ticker != "MSFT"]

    result = await refresh_symbols(
        session_factory,
        clock,
        provider=RecordingProvider(clock, without_msft),
        max_deactivation_share=1.0,
    )

    assert result.deactivated == ["MSFT"]
    msft = await _symbol(db_session, "MSFT")
    await db_session.refresh(msft)
    assert msft.is_active is False
    assert (await _symbol(db_session, "AAPL")).is_active is True


async def test_refresh_refuses_an_empty_listing(
    session_factory: Factory,
    db_session: AsyncSession,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    """NOT IN of an empty list is true for every row: it would deactivate everything."""
    result = await refresh_symbols(
        session_factory, clock, provider=RecordingProvider(clock, []), max_deactivation_share=1.0
    )

    assert result.skipped == "empty_listing"
    active = await db_session.scalars(select(Symbol).where(Symbol.is_active.is_(True)))
    assert len(active.all()) == len(SEED_TICKERS)


async def test_refresh_refuses_to_deactivate_a_large_share_of_the_universe(
    session_factory: Factory,
    db_session: AsyncSession,
    clock: FrozenClock,
    seeded_symbols: list[Symbol],
) -> None:
    only_aapl = [s for s in UNIVERSE if s.ticker == "AAPL"]

    result = await refresh_symbols(
        session_factory, clock, provider=RecordingProvider(clock, only_aapl)
    )

    assert result.skipped == "too_many_deactivations"
    active = await db_session.scalars(select(Symbol).where(Symbol.is_active.is_(True)))
    assert len(active.all()) == len(SEED_TICKERS)


# ---------------------------------------------------------------------- heartbeat


async def test_the_heartbeat_ages_out(clock: FrozenClock) -> None:
    assert not await heartbeat.worker_is_alive(clock), "no heartbeat yet"

    await heartbeat.beat(clock)
    assert await heartbeat.worker_is_alive(clock)

    clock.advance(seconds=60)
    assert await heartbeat.worker_is_alive(clock)
    clock.advance(seconds=1)
    assert not await heartbeat.worker_is_alive(clock), "older than 60 s is dead"


@pytest.mark.usefixtures("database_check_passes")
async def test_ready_names_a_missing_worker_as_degraded(client: AsyncClient) -> None:
    body = (await client.get("/ready")).json()

    assert body["checks"]["worker"] is False
    assert "worker" in body["degraded"]


@pytest.mark.usefixtures("database_check_passes")
async def test_ready_sees_a_live_worker(client: AsyncClient) -> None:
    await heartbeat.beat()

    body = (await client.get("/ready")).json()

    assert body["checks"]["worker"] is True
