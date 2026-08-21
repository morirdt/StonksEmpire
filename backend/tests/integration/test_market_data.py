"""Symbol search, quote caching, and ingest idempotency against a real database.

The provider is ``Fake`` throughout, which is the point: none of this needs a
network or a credential, and the determinism is what makes the idempotency
assertions meaningful.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.market_data.fake import FakeMarketDataProvider
from app.models.market_data import DailyBar, DailyIndicator, LatestQuote
from app.models.symbol import Symbol
from app.services.market_data_service import MarketDataService

pytestmark = pytest.mark.integration


def _service(session: AsyncSession) -> MarketDataService:
    """A service pinned to Fake, independent of whatever is configured."""
    return MarketDataService(session, provider=FakeMarketDataProvider())


# -------------------------------------------------------------------- search


async def test_search_finds_by_ticker(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/symbols", params={"search": "AAPL"})

    assert response.status_code == 200
    assert response.json()["items"][0]["ticker"] == "AAPL"


async def test_search_finds_by_name(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/symbols", params={"search": "Microsoft"})

    assert [i["ticker"] for i in response.json()["items"]] == ["MSFT"]


async def test_an_exact_ticker_ranks_first(
    db_session: AsyncSession,
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """Typing AAPL must return Apple, not whatever scores highest on similarity."""
    db_session.add(
        Symbol(
            ticker="AAPU",
            name="Apple-adjacent Leveraged Fund AAPL 2x",
            exchange="NYSEARCA",
            asset_type="etf",
            currency="USD",
        )
    )
    await db_session.commit()

    response = await auth_client.get("/api/v1/symbols", params={"search": "AAPL"})

    assert response.json()["items"][0]["ticker"] == "AAPL"


async def test_search_is_case_insensitive(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/symbols", params={"search": "aapl"})

    assert response.json()["items"][0]["ticker"] == "AAPL"


async def test_search_respects_its_limit(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/symbols", params={"search": "a", "limit": 1})

    assert len(response.json()["items"]) <= 1


async def test_a_hopeless_search_returns_nothing_rather_than_erroring(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/symbols", params={"search": "zzzzqqqq"})

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_get_one_symbol(auth_client: AsyncClient, seeded_symbols: list[Symbol]) -> None:
    response = await auth_client.get("/api/v1/symbols/AAPL")

    assert response.status_code == 200
    assert response.json()["name"] == "Apple Inc."


async def test_an_unknown_symbol_is_404(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    assert (await auth_client.get("/api/v1/symbols/NOTREAL")).status_code == 404


# -------------------------------------------------------------------- quotes


async def test_quotes_are_fetched_and_cached(
    db_session: AsyncSession,
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/quotes", params={"tickers": "AAPL,MSFT"})

    assert response.status_code == 200
    assert set(response.json()["quotes"]) == {"AAPL", "MSFT"}

    cached = await db_session.execute(select(func.count()).select_from(LatestQuote))
    assert cached.scalar_one() == 2


async def test_a_fresh_quote_is_served_from_cache_without_touching_the_provider(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    """Inside the TTL there must be no upstream call at all — that is the cache."""

    class CountingProvider(FakeMarketDataProvider):
        calls = 0

        async def get_quotes(self, tickers):  # type: ignore[no-untyped-def]
            type(self).calls += 1
            return await super().get_quotes(tickers)

    provider = CountingProvider()
    service = MarketDataService(db_session, provider=provider)

    await service.get_quotes(["AAPL"])
    assert CountingProvider.calls == 1

    await service.get_quotes(["AAPL"])
    assert CountingProvider.calls == 1, "the second read was inside the TTL"


async def test_a_stale_quote_is_refetched(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    service = _service(db_session)
    await service.get_quotes(["AAPL"])

    stored = (await db_session.execute(select(LatestQuote))).scalar_one()
    # Age the row past the TTL rather than sleeping through it.
    stored.fetched_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.commit()

    quotes = await service.get_quotes(["AAPL"])

    # Read the column rather than the entity: this table is written by a Core
    # upsert, so an entity read would come back through the identity map.
    persisted = (await db_session.execute(select(LatestQuote.fetched_at))).scalar_one()
    assert persisted > datetime.now(UTC) - timedelta(minutes=1)
    assert quotes["AAPL"].fetched_at == persisted, "the caller must see the refreshed row"


async def test_unknown_tickers_are_omitted_not_fatal(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    """One delisted name in a watchlist must not blank the whole grid."""
    response = await auth_client.get("/api/v1/quotes", params={"tickers": "AAPL,NOTREAL"})

    assert response.status_code == 200
    assert set(response.json()["quotes"]) == {"AAPL"}


async def test_a_provider_outage_serves_the_stale_cache(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    """Degrade to an old price rather than failing the request.

    The user cannot act on a provider outage, and a grid that goes blank is
    strictly less useful than one showing prices with an honest timestamp.
    """
    from app.core.exceptions import ExternalServiceError

    await _service(db_session).get_quotes(["AAPL"])

    stored = (await db_session.execute(select(LatestQuote))).scalar_one()
    stored.fetched_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.commit()

    class BrokenProvider(FakeMarketDataProvider):
        async def get_quotes(self, tickers):  # type: ignore[no-untyped-def]
            raise ExternalServiceError("upstream is down")

    quotes = await MarketDataService(db_session, provider=BrokenProvider()).get_quotes(["AAPL"])

    assert "AAPL" in quotes, "the stale row should still have been served"
    assert quotes["AAPL"].fetched_at < datetime.now(UTC) - timedelta(minutes=30)


async def test_asking_for_nothing_returns_nothing(
    auth_client: AsyncClient,
    seeded_symbols: list[Symbol],
) -> None:
    response = await auth_client.get("/api/v1/quotes", params={"tickers": ""})

    assert response.status_code == 200
    assert response.json()["quotes"] == {}


# -------------------------------------------------------------------- ingest


async def test_seeding_the_universe_is_idempotent(db_session: AsyncSession) -> None:
    service = _service(db_session)

    first = await service.seed_universe(limit=25)
    count_after_first = (
        await db_session.execute(select(func.count()).select_from(Symbol))
    ).scalar_one()

    second = await service.seed_universe(limit=25)
    count_after_second = (
        await db_session.execute(select(func.count()).select_from(Symbol))
    ).scalar_one()

    assert first == second == 25
    assert count_after_first == count_after_second


async def test_seeding_respects_its_cap(db_session: AsyncSession) -> None:
    await _service(db_session).seed_universe(limit=10)

    count = (await db_session.execute(select(func.count()).select_from(Symbol))).scalar_one()
    assert count == 10


async def test_backfill_stores_bars_and_indicators(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    symbol = seeded_symbols[0]

    bars, indicators = await _service(db_session).backfill_symbol(symbol, days=400)

    assert bars > 200
    assert indicators == bars, "one indicator row per bar, however sparse"

    stored = (
        await db_session.execute(
            select(func.count()).select_from(DailyBar).where(DailyBar.symbol_id == symbol.id)
        )
    ).scalar_one()
    assert stored == bars


async def test_running_a_backfill_twice_changes_nothing(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    """The whole point of the natural key and of Fake's determinism."""
    symbol = seeded_symbols[0]
    service = _service(db_session)

    await service.backfill_symbol(symbol, days=400)
    first = await _snapshot(db_session, symbol)

    await service.backfill_symbol(symbol, days=400)
    second = await _snapshot(db_session, symbol)

    assert first == second


async def test_a_wider_backfill_keeps_the_rows_it_already_had(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    """Overlapping windows must agree, or every re-run rewrites history."""
    symbol = seeded_symbols[0]
    service = _service(db_session)

    await service.backfill_symbol(symbol, days=200)
    narrow = {row[0]: row[1:] for row in await _bar_rows(db_session, symbol)}

    await service.backfill_symbol(symbol, days=600)
    wide = {row[0]: row[1:] for row in await _bar_rows(db_session, symbol)}

    assert len(wide) > len(narrow)
    for trade_date, values in narrow.items():
        assert wide[trade_date] == values


async def test_indicators_are_recomputed_over_the_whole_history(
    db_session: AsyncSession,
    seeded_symbols: list[Symbol],
) -> None:
    """A 200-day average that only sees the newly fetched window is not one.

    Backfilling a short window first and a long one second must leave sma_200
    populated — which only happens if the recompute reads the full stored
    series rather than just what was fetched.
    """
    symbol = seeded_symbols[0]
    service = _service(db_session)

    await service.backfill_symbol(symbol, days=100)
    await service.backfill_symbol(symbol, days=800)

    populated = (
        await db_session.execute(
            select(func.count())
            .select_from(DailyIndicator)
            .where(
                DailyIndicator.symbol_id == symbol.id,
                DailyIndicator.sma_200.is_not(None),
            )
        )
    ).scalar_one()

    assert populated > 0


async def _bar_rows(session: AsyncSession, symbol: Symbol) -> list[tuple]:
    result = await session.execute(
        select(
            DailyBar.trade_date,
            DailyBar.open,
            DailyBar.high,
            DailyBar.low,
            DailyBar.close,
            DailyBar.volume,
        )
        .where(DailyBar.symbol_id == symbol.id)
        .order_by(DailyBar.trade_date)
    )
    return [tuple(row) for row in result.all()]


async def _snapshot(session: AsyncSession, symbol: Symbol) -> tuple:
    bars = await _bar_rows(session, symbol)
    result = await session.execute(
        select(
            DailyIndicator.trade_date,
            DailyIndicator.sma_20,
            DailyIndicator.sma_200,
            DailyIndicator.rsi_14,
            DailyIndicator.macd_signal,
            DailyIndicator.atr_14,
        )
        .where(DailyIndicator.symbol_id == symbol.id)
        .order_by(DailyIndicator.trade_date)
    )
    return bars, [tuple(row) for row in result.all()]
