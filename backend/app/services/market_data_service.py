"""Reads and ingest for market data.

Owns two jobs that look different but share a provider: serving quotes to the
API cache-first, and filling the database from the scripts. Both go through the
same resilience-wrapped provider, so neither can hammer an upstream.

Per ``CLAUDE.md`` this layer raises domain errors and never ``HTTPException``,
and it owns its own transaction boundary — ``get_db`` does not commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError, NotFoundError
from app.integrations.market_data import get_market_data_provider
from app.integrations.market_data.base import MarketDataProvider, Quote
from app.models.market_data import DailyBar, DailyIndicator, LatestQuote
from app.models.symbol import Symbol
from app.repositories.bar_repository import BarRepository
from app.repositories.quote_repository import QuoteRepository
from app.repositories.symbol_repository import SymbolRepository
from app.services.chart_service import ChartWindow
from app.services.indicator_service import compute_indicators

logger = structlog.get_logger(__name__)


class MarketDataService:
    def __init__(
        self,
        session: AsyncSession,
        provider: MarketDataProvider | None = None,
    ) -> None:
        self._session = session
        self._provider = provider or get_market_data_provider()
        self._symbols = SymbolRepository(session)
        self._bars = BarRepository(session)
        self._quotes = QuoteRepository(session)
        self._settings = get_settings().market_data

    # ------------------------------------------------------------------ symbols

    async def search_symbols(self, query: str, *, limit: int = 20) -> list[Symbol]:
        return await self._symbols.search(query, limit=limit)

    async def get_symbol(self, ticker: str) -> Symbol:
        symbol = await self._symbols.get_by_ticker(ticker)
        if symbol is None:
            raise NotFoundError(f"Unknown ticker {ticker.upper()!r}.")
        return symbol

    # -------------------------------------------------------------------- chart

    async def get_bars(self, ticker: str, window: ChartWindow) -> tuple[Symbol, list[DailyBar]]:
        """OHLCV for one symbol over a resolved window, oldest first.

        An unknown ticker is a 404 here, not an empty list: "no such symbol" and
        "no history yet for this symbol" are different answers, and a chart has
        a different empty state for each.
        """
        symbol = await self.get_symbol(ticker)
        bars = await self._bars.get_range(
            symbol.id,
            start=window.start,
            end=window.end,
            limit=window.limit,
        )
        return symbol, bars

    async def get_indicators(
        self, ticker: str, window: ChartWindow
    ) -> tuple[Symbol, list[DailyIndicator]]:
        """The same window as ``get_bars``, in the same order.

        The chart aligns the two positionally, so this must truncate from the
        same end and sort the same way. It does, because both go through the one
        repository that owns that ordering.
        """
        symbol = await self.get_symbol(ticker)
        indicators = await self._bars.get_indicators(
            symbol.id,
            start=window.start,
            end=window.end,
            limit=window.limit,
        )
        return symbol, indicators

    # ------------------------------------------------------------------- quotes

    async def get_quotes(self, tickers: Sequence[str]) -> dict[str, LatestQuote]:
        """Quotes for known tickers, refetching only what has gone stale.

        Unknown tickers are omitted rather than raising: a watchlist containing
        one delisted name should still render.

        If the provider fails, whatever is cached is served instead — stale, but
        with ``quoted_at`` and ``fetched_at`` on the row so the client can say
        how old it is. Failing the whole request would take the grid down for an
        outage the user does not care about and cannot act on.
        """
        if not tickers:
            return {}

        symbols = await self._symbols.get_many_by_ticker(tickers)
        if not symbols:
            return {}
        by_id = {s.id: s for s in symbols}

        fresh = await self._quotes.get_fresh(list(by_id), ttl=self._settings.quote_ttl)
        stale_ids = [sid for sid in by_id if sid not in fresh]

        if stale_ids:
            refreshed = await self._refresh_quotes({sid: by_id[sid] for sid in stale_ids})
            fresh |= refreshed
            missing = [sid for sid in stale_ids if sid not in refreshed]
            if missing:
                # Fall back to the stale rows rather than serving nothing.
                fresh |= await self._quotes.get_many(missing)

        return {by_id[sid].ticker: quote for sid, quote in fresh.items()}

    async def _refresh_quotes(
        self, symbols: Mapping[uuid.UUID, Symbol]
    ) -> dict[uuid.UUID, LatestQuote]:
        tickers = [s.ticker for s in symbols.values()]
        try:
            quotes = await self._provider.get_quotes(tickers)
        except ExternalServiceError as exc:
            logger.warning(
                "quote_refresh_failed",
                provider=self._provider.name,
                tickers=len(tickers),
                error=str(exc),
            )
            return {}

        by_ticker = {s.ticker: sid for sid, s in symbols.items()}
        fetched: dict[uuid.UUID, Quote] = {
            by_ticker[ticker]: quote for ticker, quote in quotes.items() if ticker in by_ticker
        }
        if not fetched:
            return {}

        await self._quotes.upsert_many(fetched)
        await self._session.commit()
        return await self._quotes.get_many(list(fetched))

    # ------------------------------------------------------------------- ingest

    async def seed_universe(self, *, limit: int | None = None) -> int:
        """Fetch the provider's universe and upsert it. Safe to re-run."""
        symbols = await self._provider.list_symbols()
        capped = list(symbols)[: limit or self._settings.universe_max_symbols]

        count = await self._symbols.upsert_many(capped)
        await self._session.commit()
        logger.info("universe_seeded", provider=self._provider.name, symbols=count)
        return count

    async def backfill_symbol(
        self,
        symbol: Symbol,
        *,
        days: int,
        today: date | None = None,
    ) -> tuple[int, int]:
        """Fetch, store, and re-derive indicators for one symbol.

        Returns ``(bars, indicator_rows)``. Indicators are recomputed over the
        symbol's **whole** stored history rather than just the fetched window:
        a 200-day average that only sees the new bars is not a 200-day average,
        and the leading rows of an appended window would otherwise stay null
        forever.
        """
        end = today or datetime.now(UTC).date()
        start = end - timedelta(days=days)

        bars = await self._provider.get_daily_bars(symbol.ticker, start, end)
        stored = await self._bars.upsert_bars(symbol.id, bars)
        await self._session.commit()

        indicator_rows = await self.recompute_indicators(symbol)
        return stored, indicator_rows

    async def recompute_indicators(self, symbol: Symbol) -> int:
        """Recompute every indicator row for a symbol from its stored bars."""
        series = await self._bars.as_provider_bars(symbol.id)
        if not series:
            return 0

        rows = compute_indicators(series)
        count = await self._bars.upsert_indicators(symbol.id, rows)
        await self._session.commit()
        return count
