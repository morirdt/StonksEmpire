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
  about which days *should* exist, only about the days it was given.
* Prices are a random walk, not a simulation of anything. They trend, gap, and
  wobble enough to exercise indicator maths and to look alive in the UI.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from random import Random

from app.core.enums import AssetType
from app.core.exceptions import NotFoundError
from app.integrations.market_data.base import Bar, Quote, SymbolInfo
from app.integrations.market_data.universe import load_universe

__all__ = ["FakeMarketDataProvider"]

#: Where every generated series begins. Fixed, so the bars for a given date do
#: not depend on the window that was asked for — request 30 days or 3000 and the
#: overlapping rows are identical. Idempotent upserts depend on this.
_SERIES_EPOCH = date(2015, 1, 2)

_CENT = Decimal("0.01")


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


class FakeMarketDataProvider:
    """Deterministic pseudo-random walks, seeded by ticker."""

    name = "fake"

    def __init__(self, symbols: Sequence[SymbolInfo] | None = None) -> None:
        self._symbols = list(symbols if symbols is not None else load_universe())
        self._by_ticker = {s.ticker: s for s in self._symbols}

    # ---------------------------------------------------------------- protocol

    async def list_symbols(self) -> Sequence[SymbolInfo]:
        return list(self._symbols)

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        self._require_known(ticker)
        if start > end:
            return []
        return [bar for bar in self._series(ticker, end) if start <= bar.trade_date <= end]

    async def get_quote(self, ticker: str) -> Quote:
        self._require_known(ticker)
        today = datetime.now(UTC).date()
        series = self._series(ticker, today)
        if not series:  # pragma: no cover - only if the epoch moves past today
            raise NotFoundError(f"No pricing available for {ticker!r}.")

        latest = series[-1]
        previous_close = series[-2].close if len(series) > 1 else latest.open
        change = latest.close - previous_close
        change_percent = (
            (change / previous_close * 100).quantize(_CENT) if previous_close else Decimal("0.00")
        )
        return Quote(
            ticker=ticker.upper(),
            price=latest.close,
            change=change,
            change_percent=change_percent,
            day_open=latest.open,
            day_high=latest.high,
            day_low=latest.low,
            previous_close=previous_close,
            volume=latest.volume,
            quoted_at=datetime.combine(latest.trade_date, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=21),  # ~16:00 ET close
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
