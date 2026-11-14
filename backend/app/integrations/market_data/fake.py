"""A provider that invents plausible market data, deterministically.

This is not a test double bolted on afterwards — it is the default provider and
the reason the rest of Phase 2 could be built at all. It needs no network, no
credential, and no quota, so the data layer, the ingest scripts, the endpoints
and the whole watchlist feature are developed and tested against it.

**Determinism is the whole contract.** A ticker's series is seeded from the
ticker itself, so ``AAPL`` produces the same bars on every machine, in every
process, forever. That is what makes ingest idempotency testable: run a backfill
twice and the second run must change nothing, which is only meaningful if the
source agrees with itself.

Two simplifications worth knowing about, both deliberate:

* The calendar is weekdays only — no market holidays. Nothing downstream reasons
  about which days *should* exist, only about the days it was given. The real
  calendar (``app.core.market_calendar``) decides when the worker runs, not
  which bars exist; on a holiday this provider invents a bar, and nothing minds.
* Prices are a random walk, not a simulation of anything. They trend, gap, and
  wobble enough to exercise indicator maths and to look alive in the UI.

**Time is an input.** The provider takes a clock, and two things follow from it:

* The daily series ends at the last *completed* session. Before 16:00 ET today
  has no bar, because a bar served at 10 a.m. would let the nightly ingest and
  the end-of-day alerts "know" a close that has not happened.
* During a session, a quote follows a path through today's bar (see
  :func:`intraday_path`), so prices move and an intraday alert can fire in
  development. At 16:00 the quote equals the bar exactly.
"""

from __future__ import annotations

import itertools
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from random import Random
from typing import Final

from app.core.enums import AssetType
from app.core.exceptions import NotFoundError
from app.core.market_calendar import (
    MARKET_TZ,
    SESSION_CLOSE,
    is_market_open,
    last_completed_session,
    session_bounds,
)
from app.integrations.market_data.base import Bar, Quote, SymbolInfo
from app.integrations.market_data.universe import load_universe

__all__ = ["FakeMarketDataProvider", "IntradayPoint", "intraday_path"]

#: Where every generated series begins. Fixed, so the bars for a given date do
#: not depend on the window that was asked for — request 30 days or 3000 and the
#: overlapping rows are identical. Idempotent upserts depend on this.
_SERIES_EPOCH = date(2015, 1, 2)

_CENT = Decimal("0.01")

#: 09:30 to 16:00 is 390 minutes. Index 0 is the open, index 390 the close.
SESSION_MINUTES: Final = 390


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _seed_for(ticker: str) -> int:
    """A stable seed for a ticker.

    ``hash()`` is salted per process and would give a different series on every
    restart; CRC32 does not, which is the entire requirement here.
    """
    return zlib.crc32(ticker.upper().encode())


def _trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


@dataclass(frozen=True, slots=True)
class IntradayPoint:
    """The state of one session at one minute."""

    price: Decimal
    day_high: Decimal
    day_low: Decimal
    volume: int


def intraday_path(ticker: str, bar: Bar) -> list[IntradayPoint]:
    """Minute-by-minute prices through ``bar``, from 09:30 (index 0) to 16:00 (390).

    A piecewise bridge through four fixed points: ``open`` at 09:30, the bar's
    ``high`` and ``low`` at two seeded minutes, and ``close`` at 16:00. Between
    them it wanders (a Brownian bridge, so the wander is exactly zero at every
    fixed point), then it is clamped to ``[low, high]`` and quantised to cents.

    Seeded by ``crc32(f"{ticker}:{date}")``, so a given minute always has the
    same price. Running extremes and cumulative volume are carried on each
    point, and at index 390 they equal the bar: the high and low have both been
    visited by then, and the whole day's volume has traded.
    """
    rng = Random(zlib.crc32(f"{ticker.upper()}:{bar.trade_date.isoformat()}".encode()))  # noqa: S311

    first, second = sorted(rng.sample(range(1, SESSION_MINUTES), 2))
    high_first = rng.random() < 0.5
    anchors = [
        (0, bar.open),
        (first, bar.high if high_first else bar.low),
        (second, bar.low if high_first else bar.high),
        (SESSION_MINUTES, bar.close),
    ]

    span = bar.high - bar.low
    raw: list[Decimal] = []
    for (m0, v0), (m1, v1) in itertools.pairwise(anchors):
        length = m1 - m0
        # A random walk over the segment, minus the straight line from its first
        # value to its last, is zero at both ends: a bridge.
        walk = [0.0]
        for _ in range(length):
            walk.append(walk[-1] + rng.gauss(0.0, 1.0))
        scale = span * Decimal("0.04")
        for i in range(length):
            bridge = walk[i] - walk[length] * i / length
            linear = v0 + (v1 - v0) * Decimal(i) / Decimal(length)
            raw.append(linear + scale * Decimal(str(round(bridge, 6))))
    raw.append(bar.close)

    points: list[IntradayPoint] = []
    running_high = running_low = bar.open
    for minute, value in enumerate(raw):
        price = min(max(value, bar.low), bar.high).quantize(_CENT)
        running_high = max(running_high, price)
        running_low = min(running_low, price)
        points.append(
            IntradayPoint(
                price=price,
                day_high=running_high,
                day_low=running_low,
                volume=bar.volume * minute // SESSION_MINUTES,
            )
        )
    return points


class FakeMarketDataProvider:
    """Deterministic pseudo-random walks, seeded by ticker."""

    name = "fake"

    def __init__(
        self,
        symbols: Sequence[SymbolInfo] | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._symbols = list(symbols if symbols is not None else load_universe())
        self._by_ticker = {s.ticker: s for s in self._symbols}
        self._clock = clock

    # ---------------------------------------------------------------- protocol

    async def list_symbols(self) -> Sequence[SymbolInfo]:
        return list(self._symbols)

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        self._require_known(ticker)
        # Nothing after the last completed session: today's bar does not exist
        # until today's close.
        end = min(end, last_completed_session(self._clock()))
        if start > end:
            return []
        return [bar for bar in self._series(ticker, end) if start <= bar.trade_date <= end]

    async def get_quote(self, ticker: str) -> Quote:
        self._require_known(ticker)
        now = self._clock()
        if is_market_open(now):
            return self._intraday_quote(ticker, now)
        return self._closing_quote(ticker, last_completed_session(now))

    def _intraday_quote(self, ticker: str, now: datetime) -> Quote:
        today = now.astimezone(MARKET_TZ).date()
        series = self._series(ticker, today)
        bar, previous_close = series[-1], series[-2].close
        bounds = session_bounds(today)
        if bounds is None:  # pragma: no cover - is_market_open said there is one
            raise RuntimeError(f"{today} has no session, but the market is open.")

        minute = int((now - bounds[0]).total_seconds() // 60)
        point = intraday_path(ticker, bar)[minute]
        return self._quote(
            ticker,
            price=point.price,
            day_open=bar.open,
            day_high=point.day_high,
            day_low=point.day_low,
            volume=point.volume,
            previous_close=previous_close,
            quoted_at=bounds[0] + timedelta(minutes=minute),
        )

    def _closing_quote(self, ticker: str, session: date) -> Quote:
        series = self._series(ticker, session)
        if not series:  # pragma: no cover - only if the epoch moves past today
            raise NotFoundError(f"No pricing available for {ticker!r}.")
        bar = series[-1]
        previous_close = series[-2].close if len(series) > 1 else bar.open
        close_at = datetime.combine(bar.trade_date, SESSION_CLOSE, tzinfo=MARKET_TZ)
        return self._quote(
            ticker,
            price=bar.close,
            day_open=bar.open,
            day_high=bar.high,
            day_low=bar.low,
            volume=bar.volume,
            previous_close=previous_close,
            quoted_at=close_at.astimezone(UTC),
        )

    @staticmethod
    def _quote(
        ticker: str,
        *,
        price: Decimal,
        day_open: Decimal,
        day_high: Decimal,
        day_low: Decimal,
        volume: int,
        previous_close: Decimal,
        quoted_at: datetime,
    ) -> Quote:
        change = price - previous_close
        change_percent = (
            (change / previous_close * 100).quantize(_CENT) if previous_close else Decimal("0.00")
        )
        return Quote(
            ticker=ticker.upper(),
            price=price,
            change=change,
            change_percent=change_percent,
            day_open=day_open,
            day_high=day_high,
            day_low=day_low,
            previous_close=previous_close,
            volume=volume,
            quoted_at=quoted_at,
        )

    async def get_quotes(self, tickers: Sequence[str]) -> Mapping[str, Quote]:
        quotes: dict[str, Quote] = {}
        for ticker in tickers:
            try:
                quotes[ticker.upper()] = await self.get_quote(ticker)
            except NotFoundError:
                # Omitted rather than raised: one delisted name in a watchlist
                # must not blank the whole grid.
                continue
        return quotes

    # ----------------------------------------------------------------- internals

    def _require_known(self, ticker: str) -> None:
        if ticker.upper() not in self._by_ticker:
            raise NotFoundError(f"Unknown ticker {ticker.upper()!r}.")

    def _series(self, ticker: str, end: date) -> list[Bar]:
        """The full walk from the epoch to ``end``.

        Regenerated per call rather than cached: it is a few thousand cheap
        iterations, and a cache would be one more thing to invalidate in a
        process that may run for days.
        """
        rng = Random(_seed_for(ticker))  # noqa: S311 - not cryptographic, and must not be
        symbol = self._by_ticker[ticker.upper()]

        # ETFs start cheaper and drift less than the average single name.
        price = Decimal(str(round(rng.uniform(18.0, 260.0), 2)))
        drift = Decimal("0.00018") if symbol.asset_type is AssetType.ETF else Decimal("0.00028")
        volatility = 0.010 if symbol.asset_type is AssetType.ETF else 0.019
        base_volume = rng.randint(500_000, 40_000_000)

        bars: list[Bar] = []
        for trade_date in _trading_days(_SERIES_EPOCH, end):
            previous_close = price
            step = Decimal(str(round(rng.gauss(0.0, volatility), 6))) + drift
            close = (previous_close * (Decimal(1) + step)).quantize(_CENT)
            # A floor, so a long unlucky walk cannot reach zero or go negative.
            close = max(close, Decimal("1.00"))

            gap = Decimal(str(round(rng.gauss(0.0, volatility / 3), 6)))
            open_ = max((previous_close * (Decimal(1) + gap)).quantize(_CENT), Decimal("1.00"))
            spread = Decimal(str(round(abs(rng.gauss(0.0, volatility / 2)), 6)))
            high = (max(open_, close) * (Decimal(1) + spread)).quantize(_CENT)
            low = (min(open_, close) * (Decimal(1) - spread)).quantize(_CENT)
            low = max(min(low, open_, close), Decimal("0.01"))

            bars.append(
                Bar(
                    trade_date=trade_date,
                    open=open_,
                    high=max(high, open_, close),
                    low=low,
                    close=close,
                    volume=int(base_volume * rng.uniform(0.4, 1.9)),
                    is_adjusted=True,
                )
            )
            price = close

        return bars
