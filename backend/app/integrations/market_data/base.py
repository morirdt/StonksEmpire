"""The market data provider seam.

Everything upstream of this module is a third party we do not control;
everything downstream of it speaks in the dataclasses defined here. That is the
whole point: no provider's field names, error shapes, or JSON quirks may leak
past this file into the service layer.

Implementations are related only by this protocol — there is no shared
behaviour worth inheriting, so it is a ``typing.Protocol`` rather than an ABC.
Resilience (rate limiting, retry, circuit breaking) is *not* an implementation's
job either; it is layered on from ``resilience.py``, so a provider is only ever
responsible for talking to its API and translating the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.core.enums import AssetType

__all__ = [
    "Bar",
    "MarketDataProvider",
    "Quote",
    "SymbolInfo",
]


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """One tradable instrument as a provider describes it."""

    ticker: str
    name: str
    exchange: str | None = None
    asset_type: AssetType = AssetType.COMMON_STOCK
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class Bar:
    """One daily OHLCV bar.

    ``is_adjusted`` travels with the bar rather than being assumed per provider,
    because it is a property of the endpoint that produced it. Recording it lets
    a later backfill tell which rows a split has invalidated.
    """

    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    is_adjusted: bool = True


@dataclass(frozen=True, slots=True)
class Quote:
    """A point-in-time price for one symbol.

    Two timestamps, because they answer different questions. ``quoted_at`` is
    the provider's own — how old is this price. ``fetched_at`` is ours, and it
    is what the cache TTL is measured against. Conflating them makes a quiet
    weekend look identical to a broken ingest.
    """

    ticker: str
    price: Decimal
    change: Decimal | None = None
    change_percent: Decimal | None = None
    day_open: Decimal | None = None
    day_high: Decimal | None = None
    day_low: Decimal | None = None
    previous_close: Decimal | None = None
    volume: int | None = None
    quoted_at: datetime | None = None


@runtime_checkable
class MarketDataProvider(Protocol):
    """What every market data source must offer.

    Error contract, which the parameterized contract suite enforces:

    * An unknown ticker raises ``NotFoundError``. A bad symbol is a client
      mistake, not an upstream outage, and it must not open the circuit breaker.
    * Every other failure — timeout, connection error, malformed payload, any
      upstream ``5xx`` — raises ``ExternalServiceError``, which the HTTP layer
      already maps to a ``502``.
    """

    @property
    def name(self) -> str:
        """Stable identifier, used for per-provider breaker and limiter state."""
        ...

    async def list_symbols(self) -> Sequence[SymbolInfo]:
        """The tradable universe this provider knows about."""
        ...

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        """Daily OHLCV over ``[start, end]`` inclusive, oldest first.

        Adjusted prices wherever the provider offers them: unadjusted history
        makes every chart lie the moment a split happens.
        """
        ...

    async def get_quote(self, ticker: str) -> Quote:
        """The current price for one symbol."""
        ...

    async def get_quotes(self, tickers: Sequence[str]) -> Mapping[str, Quote]:
        """Current prices for many symbols, keyed by ticker.

        A provider without a batch endpoint fans out internally. Tickers the
        provider cannot price are omitted rather than raising, so one delisted
        name in a watchlist does not blank the whole grid.
        """
        ...
