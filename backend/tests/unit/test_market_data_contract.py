"""The provider contract, run against every implementation.

This suite is the only thing keeping the implementations honestly
interchangeable. It asserts on the *protocol* — shapes, ordering, invariants,
and which failure mode each error condition produces — and never on values a
particular provider happens to return.

Adding a real provider means adding one entry to ``PROVIDERS`` below (with its
HTTP mocked at the transport layer against recorded fixtures — no live network
in tests, ever). If the new provider does not pass unchanged, the seam has
leaked and the fix belongs in the provider, not here.

Every provider is built with a clock, and the suite freezes it. "Today has no
bar before the close" and "the 16:00 quote equals the bar" are contract, not
``Fake`` trivia: they are what lets the nightly ingest and the last quote of the
day agree about the same session.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.exceptions import NotFoundError
from app.core.market_calendar import MARKET_TZ
from app.integrations.market_data.base import MarketDataProvider
from app.integrations.market_data.fake import (
    SESSION_MINUTES,
    FakeMarketDataProvider,
    intraday_path,
)


class FrozenClock:
    """A clock that says whatever the test last set it to."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def set_et(self, *args: int) -> None:
        self.at = datetime(*args, tzinfo=MARKET_TZ).astimezone(UTC)  # type: ignore[misc]


#: Every implementation under contract, built with a clock. One entry today;
#: see the module docstring.
PROVIDERS: dict[str, Callable[[FrozenClock], MarketDataProvider]] = {
    "fake": lambda clock: FakeMarketDataProvider(clock=clock),
}

#: A Wednesday, mid-session. The fixture's default instant.
SESSION_DAY = date(2026, 11, 18)


@pytest.fixture
def clock() -> FrozenClock:
    frozen = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    frozen.set_et(2026, 11, 18, 11, 0)
    return frozen


@pytest.fixture(params=sorted(PROVIDERS), ids=sorted(PROVIDERS))
def provider(request: pytest.FixtureRequest, clock: FrozenClock) -> MarketDataProvider:
    return PROVIDERS[request.param](clock)


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


# ------------------------------------------------------------ time-dependent


async def test_today_has_no_bar_before_the_close(
    provider: MarketDataProvider, clock: FrozenClock
) -> None:
    ticker = (await provider.list_symbols())[0].ticker

    clock.set_et(2026, 11, 18, 15, 59)
    before = await provider.get_daily_bars(ticker, SESSION_DAY - timedelta(days=7), SESSION_DAY)
    clock.set_et(2026, 11, 18, 16, 0)
    after = await provider.get_daily_bars(ticker, SESSION_DAY - timedelta(days=7), SESSION_DAY)

    assert before and before[-1].trade_date < SESSION_DAY
    assert after[-1].trade_date == SESSION_DAY


async def test_the_closing_quote_equals_the_bar(
    provider: MarketDataProvider, clock: FrozenClock
) -> None:
    """Otherwise the last quote of the day and the stored bar disagree."""
    ticker = (await provider.list_symbols())[0].ticker
    clock.set_et(2026, 11, 18, 16, 0)

    quote = await provider.get_quote(ticker)
    (bar,) = await provider.get_daily_bars(ticker, SESSION_DAY, SESSION_DAY)

    assert quote.price == bar.close
    assert quote.day_open == bar.open
    assert quote.day_high == bar.high
    assert quote.day_low == bar.low
    assert quote.volume == bar.volume


async def test_an_intraday_quote_sits_inside_its_own_range(
    provider: MarketDataProvider, clock: FrozenClock
) -> None:
    ticker = (await provider.list_symbols())[0].ticker

    quote = await provider.get_quote(ticker)

    assert quote.day_low is not None and quote.day_high is not None
    assert quote.day_low <= quote.price <= quote.day_high


class TestFakeIntradayPath:
    """The path ``Fake`` walks through a session. Fake-only, like determinism."""

    @pytest.fixture
    async def bar_and_path(self) -> tuple[object, list[object]]:
        provider = FakeMarketDataProvider(clock=lambda: datetime(2026, 11, 18, 22, 0, tzinfo=UTC))
        (bar,) = await provider.get_daily_bars("AAPL", SESSION_DAY, SESSION_DAY)
        return bar, intraday_path("AAPL", bar)

    def test_the_path_starts_at_the_open_and_ends_at_the_close(self, bar_and_path) -> None:
        bar, path = bar_and_path

        assert len(path) == SESSION_MINUTES + 1
        assert path[0].price == bar.open
        assert path[-1].price == bar.close

    def test_the_path_never_leaves_the_bar(self, bar_and_path) -> None:
        bar, path = bar_and_path

        assert all(bar.low <= p.price <= bar.high for p in path)
        assert all(p.price == p.price.quantize(bar.close) for p in path), "cents only"

    def test_the_running_extremes_reach_the_bar_by_the_close(self, bar_and_path) -> None:
        bar, path = bar_and_path

        assert (path[-1].day_high, path[-1].day_low, path[-1].volume) == (
            bar.high,
            bar.low,
            bar.volume,
        )
        highs = [p.day_high for p in path]
        assert highs == sorted(highs), "a running high never falls"

    def test_the_price_actually_moves(self, bar_and_path) -> None:
        """The whole reason for the path: a flat price cannot fire an alert."""
        _, path = bar_and_path

        assert len({p.price for p in path}) > 20

    async def test_a_given_minute_is_deterministic(self, clock: FrozenClock) -> None:
        clock.set_et(2026, 11, 18, 11, 17, 5)
        first = await FakeMarketDataProvider(clock=clock).get_quote("AAPL")
        clock.set_et(2026, 11, 18, 11, 17, 55)
        second = await FakeMarketDataProvider(clock=clock).get_quote("AAPL")

        assert first == second, "the same minute, so the same quote"

    async def test_consecutive_minutes_differ_somewhere_in_the_day(
        self, clock: FrozenClock
    ) -> None:
        provider = FakeMarketDataProvider(clock=clock)
        prices = set()
        for minute in range(0, 60, 5):
            clock.set_et(2026, 11, 18, 10, minute)
            prices.add((await provider.get_quote("AAPL")).price)

        assert len(prices) > 1

    async def test_before_the_open_the_quote_is_yesterdays_close(self, clock: FrozenClock) -> None:
        provider = FakeMarketDataProvider(clock=clock)
        clock.set_et(2026, 11, 18, 9, 0)

        quote = await provider.get_quote("AAPL")
        (yesterday,) = await provider.get_daily_bars("AAPL", date(2026, 11, 17), date(2026, 11, 17))

        assert quote.price == yesterday.close
        assert quote.quoted_at == datetime(2026, 11, 17, 21, 0, tzinfo=UTC)


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
