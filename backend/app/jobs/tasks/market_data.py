"""The ingest jobs: keep quotes, bars, and the universe current.

Each job takes a session factory and a clock and knows nothing about
APScheduler, so tests call it directly at a frozen instant. Each one starts by
asking the market calendar whether it should run at all. Cron cannot express
"weekdays at 17:30 unless it is Good Friday", so the rule lives in the calendar
and nowhere else.

Each job returns a small result describing what it did. ``run_job`` logs it, so
the hot-set size and per-run duration the spec asks for are in every run's log
line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import get_settings
from app.core.events import Publisher, publish_event
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.market_calendar import (
    MARKET_TZ,
    is_market_open,
    is_trading_day,
    last_completed_session,
)
from app.integrations.market_data import get_market_data_provider
from app.integrations.market_data.base import MarketDataProvider
from app.repositories.bar_repository import BarRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.market_data_service import MarketDataService

__all__ = [
    "IngestResult",
    "PollResult",
    "RefreshResult",
    "ingest_eod",
    "poll_quotes",
    "refresh_symbols",
]

logger = structlog.stdlib.get_logger(__name__)

type SessionFactory = async_sessionmaker[AsyncSession]


# ----------------------------------------------------------------- poll quotes


@dataclass(frozen=True, slots=True)
class PollResult:
    skipped: str | None = None
    hot_set: int = 0
    refreshed: int = 0


async def poll_quotes(
    session_factory: SessionFactory,
    clock: Clock,
    *,
    closing_run: bool = False,
    provider: MarketDataProvider | None = None,
    publish: Publisher = publish_event,
) -> PollResult:
    """Re-quote the hot set, then publish a ``quotes_updated`` hint.

    Runs while the market is open. The 16:05 ``closing_run`` instead runs on any
    trading day, and picks up the closing prices that the last in-session poll,
    a minute or more before 16:00, could not have seen.

    A provider failure refreshes nothing and publishes nothing. The grid keeps
    serving stale quotes with an honest ``fetched_at``, which is right for a
    grid.
    """
    now = clock()
    if closing_run:
        if not is_trading_day(now.astimezone(MARKET_TZ).date()):
            return PollResult(skipped="not_a_trading_day")
    elif not is_market_open(now):
        return PollResult(skipped="market_closed")

    async with session_factory() as session:
        hot_set = await SymbolRepository(session).list_hot_set()
        if not hot_set:
            return PollResult(hot_set=0)

        service = MarketDataService(session, provider=provider)
        refreshed = await service.refresh_quotes(hot_set, fetched_at=clock())

    if refreshed:
        by_id = {s.id: s.ticker for s in hot_set}
        await publish(
            {
                "type": "quotes_updated",
                "fetched_at": max(q.fetched_at for q in refreshed.values()).isoformat(),
                "tickers": sorted(by_id[sid] for sid in refreshed),
            }
        )
    return PollResult(hot_set=len(hot_set), refreshed=len(refreshed))


# ------------------------------------------------------------------ ingest eod


@dataclass(frozen=True, slots=True)
class IngestResult:
    skipped: str | None = None
    session: date | None = None
    symbols: int = 0
    ingested: int = 0
    up_to_date: int = 0
    bars: int = 0
    no_history: list[str] = field(default_factory=list)
    too_far_behind: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def ingest_eod(
    session_factory: SessionFactory,
    clock: Clock,
    *,
    provider: MarketDataProvider | None = None,
    max_gap_days: int | None = None,
) -> IngestResult:
    """Top up each active symbol's bars through the last completed session.

    Incremental and bounded. A symbol is fetched from the day after its last
    stored bar, and only if that bar is at most ``max_gap_days`` old. A symbol
    with no bars at all, or one further behind, is logged and skipped: it needs
    a manual ``make backfill``, which the worker never schedules.

    Indicators are still recomputed over each symbol's **whole** stored series,
    as ``CLAUDE.md`` requires. That is most of this job's cost.

    A second run on the same night finds every symbol up to date and writes
    nothing.
    """
    now = clock()
    if not is_trading_day(now.astimezone(MARKET_TZ).date()):
        return IngestResult(skipped="not_a_trading_day")

    gap_limit = timedelta(days=max_gap_days or get_settings().worker.max_ingest_gap_days)
    target = last_completed_session(now)

    no_history: list[str] = []
    too_far: list[str] = []
    failed: list[str] = []
    ingested = up_to_date = bars = 0

    async with session_factory() as session:
        symbols = await SymbolRepository(session).list_active()
        latest = await BarRepository(session).latest_dates([s.id for s in symbols])
        service = MarketDataService(session, provider=provider)

        for symbol in symbols:
            last = latest.get(symbol.id)
            if last is None:
                no_history.append(symbol.ticker)
                continue
            if last >= target:
                up_to_date += 1
                continue
            if target - last > gap_limit:
                too_far.append(symbol.ticker)
                continue
            try:
                stored, _ = await service.ingest_range(symbol, last + timedelta(days=1), target)
            except (ExternalServiceError, NotFoundError) as exc:
                # One symbol the provider will not serve must not stop the rest.
                # The fetch is what failed, before anything was written, so
                # there is nothing to roll back. A rollback would also expire
                # every loaded Symbol, and the next attribute read would then
                # try a lazy load that asyncio does not allow.
                logger.warning("ingest_symbol_failed", ticker=symbol.ticker, error=str(exc))
                failed.append(symbol.ticker)
                continue
            ingested += 1
            bars += stored

    if no_history or too_far:
        logger.warning(
            "ingest_needs_backfill",
            no_history=len(no_history),
            too_far_behind=len(too_far),
            hint="run `make backfill` for these symbols",
        )
    return IngestResult(
        session=target,
        symbols=len(symbols),
        ingested=ingested,
        up_to_date=up_to_date,
        bars=bars,
        no_history=no_history,
        too_far_behind=too_far,
        failed=failed,
    )


# ------------------------------------------------------------- refresh symbols

#: The most of the active universe one refresh may deactivate. A provider that
#: suddenly lists half as many symbols is far more likely to be paginating
#: wrongly than to have seen half the market delist overnight, and deactivating
#: a symbol disables every alert on it.
_MAX_DEACTIVATION_SHARE: Final = 0.10


@dataclass(frozen=True, slots=True)
class RefreshResult:
    skipped: str | None = None
    listed: int = 0
    upserted: int = 0
    deactivated: list[str] = field(default_factory=list)


async def refresh_symbols(
    session_factory: SessionFactory,
    clock: Clock,
    *,
    provider: MarketDataProvider | None = None,
    max_deactivation_share: float = _MAX_DEACTIVATION_SHARE,
) -> RefreshResult:
    """Upsert the provider's universe, and deactivate what it no longer lists.

    Deactivates and never deletes, because watchlists and alerts reference
    symbols by foreign key. Refuses outright when the listing is empty or would
    deactivate more than ``max_deactivation_share`` of the universe, since
    either is likelier to be a provider fault than a market event.
    """
    del clock  # Unused: the refresh is not gated on market hours.
    source = provider or get_market_data_provider()
    listed = list(await source.list_symbols())
    if not listed:
        logger.error("symbol_refresh_refused", reason="empty_listing", provider=source.name)
        return RefreshResult(skipped="empty_listing")

    listed_tickers = {s.ticker.upper() for s in listed}
    cap = get_settings().market_data.universe_max_symbols

    async with session_factory() as session:
        repository = SymbolRepository(session)
        active = await repository.list_active()
        vanishing = [s.ticker for s in active if s.ticker not in listed_tickers]
        if active and len(vanishing) > len(active) * max_deactivation_share:
            logger.error(
                "symbol_refresh_refused",
                reason="too_many_deactivations",
                would_deactivate=len(vanishing),
                active=len(active),
            )
            return RefreshResult(skipped="too_many_deactivations", listed=len(listed))

        upserted = await repository.upsert_many(listed[:cap])
        deactivated = await repository.deactivate_missing(sorted(listed_tickers))
        await session.commit()

    tickers = sorted(s.ticker for s in deactivated)
    if tickers:
        logger.info("symbols_deactivated", tickers=tickers)
    return RefreshResult(listed=len(listed), upserted=upserted, deactivated=tickers)
