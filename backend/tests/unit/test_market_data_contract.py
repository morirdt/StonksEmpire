"""The provider contract, run against every implementation.

This suite is the only thing keeping the implementations honestly
interchangeable. It asserts on the *protocol* — shapes, ordering, invariants,
and which failure mode each error condition produces — and never on values a
particular provider happens to return.

Adding a real provider means adding one entry to ``PROVIDERS`` below (with its
HTTP mocked at the transport layer against recorded fixtures — no live network
in tests, ever). If the new provider does not pass unchanged, the seam has
leaked and the fix belongs in the provider, not here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest

from app.core.exceptions import NotFoundError
from app.integrations.market_data.base import MarketDataProvider
from app.integrations.market_data.fake import FakeMarketDataProvider

#: Every implementation under contract. One entry today; see the module docstring.
PROVIDERS: dict[str, Callable[[], MarketDataProvider]] = {
    "fake": FakeMarketDataProvider,
}


@pytest.fixture(params=sorted(PROVIDERS), ids=sorted(PROVIDERS))
def provider(request: pytest.FixtureRequest) -> MarketDataProvider:
    return PROVIDERS[request.param]()


UNKNOWN_TICKER = "NOTAREALTICKER"


async def test_satisfies_the_protocol(provider: MarketDataProvider) -> None:
    assert isinstance(provider, MarketDataProvider)
    assert provider.name


async def test_list_symbols_returns_a_usable_universe(provider: MarketDataProvider) -> None:
    symbols = await provider.list_symbols()

    assert symbols, "a provider with an empty universe is useless downstream"
    tickers = [s.ticker for s in symbols]
    assert len(set(tickers)) == len(tickers), "duplicate tickers would break the upsert key"
    assert all(t == t.upper() for t in tickers), "tickers are stored uppercase"
    assert all(s.name for s in symbols)


async def test_daily_bars_are_ordered_and_within_the_window(
    provider: MarketDataProvider,
) -> None:
    ticker = (await provider.list_symbols())[0].ticker
    start, end = date(2024, 1, 1), date(2024, 3, 31)

    bars = await provider.get_daily_bars(ticker, start, end)

    assert bars
    dates = [b.trade_date for b in bars]
    assert dates == sorted(dates), "bars are oldest first"
    assert len(set(dates)) == len(dates), "one bar per day, or the PK collides"
    assert all(start <= d <= end for d in dates), "the window is inclusive on both ends"


async def test_daily_bars_respect_ohlc_invariants(provider: MarketDataProvider) -> None:
    ticker = (await provider.list_symbols())[0].ticker

    bars = await provider.get_daily_bars(ticker, date(2024, 1, 1), date(2024, 6, 30))

    for bar in bars:
        assert bar.low <= bar.open <= bar.high, f"{bar.trade_date}: open outside its range"
        assert bar.low <= bar.close <= bar.high, f"{bar.trade_date}: close outside its range"
        assert bar.low > 0, "a non-positive price would break every return calculation"
        assert bar.volume >= 0


async def test_overlapping_windows_agree(provider: MarketDataProvider) -> None:
    """The same trading day must produce the same bar in any request.

    Ingest idempotency rests on this: a backfill that asked for a wider window
    must not rewrite the rows an earlier, narrower one already stored.
    """
    ticker = (await provider.list_symbols())[0].ticker

    wide = await provider.get_daily_bars(ticker, date(2023, 1, 1), date(2024, 12, 31))
    narrow = await provider.get_daily_bars(ticker, date(2024, 3, 1), date(2024, 3, 31))

    by_date = {b.trade_date: b for b in wide}
    assert narrow, "the narrow window should not be empty"
    for bar in narrow:
        assert by_date[bar.trade_date] == bar


async def test_an_empty_window_is_empty_not_an_error(provider: MarketDataProvider) -> None:
    ticker = (await provider.list_symbols())[0].ticker

    assert await provider.get_daily_bars(ticker, date(2024, 6, 30), date(2024, 6, 1)) == []


async def test_get_quote_prices_a_known_symbol(provider: MarketDataProvider) -> None:
    ticker = (await provider.list_symbols())[0].ticker

    quote = await provider.get_quote(ticker)

    assert quote.ticker == ticker
    assert quote.price > 0
    if quote.quoted_at is not None:
        assert quote.quoted_at.tzinfo is not None, "naive timestamps lose meaning in UTC storage"


async def test_get_quotes_returns_every_known_ticker(provider: MarketDataProvider) -> None:
    tickers = [s.ticker for s in (await provider.list_symbols())[:5]]

    quotes = await provider.get_quotes(tickers)

    assert set(quotes) == set(tickers)
    assert all(quotes[t].ticker == t for t in tickers)


async def test_get_quotes_omits_unknown_tickers_rather_than_failing(
    provider: MarketDataProvider,
) -> None:
    """One delisted name in a watchlist must not blank the whole grid."""
    known = (await provider.list_symbols())[0].ticker

    quotes = await provider.get_quotes([known, UNKNOWN_TICKER])

    assert known in quotes
    assert UNKNOWN_TICKER not in quotes


async def test_unknown_ticker_is_not_found_not_an_outage(provider: MarketDataProvider) -> None:
    """A bad ticker is a caller mistake, so it must not look like a 502.

    This is what stops one typo from counting toward the circuit breaker.
    """
    with pytest.raises(NotFoundError):
        await provider.get_quote(UNKNOWN_TICKER)

    with pytest.raises(NotFoundError):
        await provider.get_daily_bars(UNKNOWN_TICKER, date(2024, 1, 1), date(2024, 1, 31))


async def test_tickers_are_matched_case_insensitively(provider: MarketDataProvider) -> None:
    ticker = (await provider.list_symbols())[0].ticker

    quote = await provider.get_quote(ticker.lower())

    assert quote.ticker == ticker.upper()


class TestFakeDeterminism:
    """Guarantees specific to ``Fake``, which the others cannot make.

    These are not part of the shared contract — a real provider's prices move —
    but they are load-bearing for every test downstream of this one.
    """

    async def test_the_same_ticker_yields_the_same_series(self) -> None:
        first = await FakeMarketDataProvider().get_daily_bars(
            "AAPL", date(2024, 1, 1), date(2024, 2, 29)
        )
        second = await FakeMarketDataProvider().get_daily_bars(
            "AAPL", date(2024, 1, 1), date(2024, 2, 29)
        )

        assert first == second

    async def test_different_tickers_yield_different_series(self) -> None:
        window = (date(2024, 1, 1), date(2024, 2, 29))
        provider = FakeMarketDataProvider()

        apple = await provider.get_daily_bars("AAPL", *window)
        msft = await provider.get_daily_bars("MSFT", *window)

        assert [b.close for b in apple] != [b.close for b in msft]

    async def test_the_calendar_skips_weekends(self) -> None:
        bars = await FakeMarketDataProvider().get_daily_bars(
            "AAPL", date(2024, 1, 1), date(2024, 1, 31)
        )

        assert all(b.trade_date.weekday() < 5 for b in bars)

    async def test_two_years_covers_a_200_day_window(self) -> None:
        """The history depth Phase 2 settled on has to leave sma_200 room."""
        end = date(2024, 12, 31)
        bars = await FakeMarketDataProvider().get_daily_bars("AAPL", end - timedelta(days=730), end)

        assert len(bars) > 200
