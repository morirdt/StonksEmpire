"""API representations of symbols, quotes, bars, and indicators."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.market_data import LatestQuote


class SymbolResponse(BaseModel):
    """One instrument in the universe."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    name: str
    exchange: str | None
    asset_type: str
    currency: str
    is_active: bool


class QuoteResponse(BaseModel):
    """A price, with both timestamps.

    ``quoted_at`` is the provider's view of when this price was true;
    ``fetched_at`` is when we last asked. The client needs both to render "as
    of" honestly — a stale weekend price is not the same thing as a broken
    ingest, and a UI that only has one timestamp cannot tell the difference.

    Every price is a string over the wire, because ``Decimal`` serialised as a
    JSON number would be parsed back into a float by every JavaScript client
    and quietly lose precision.
    """

    model_config = ConfigDict(from_attributes=True)

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
    fetched_at: datetime

    @classmethod
    def from_row(cls, ticker: str, row: LatestQuote) -> QuoteResponse:
        """Build from a stored quote, which carries a symbol id but no ticker."""
        return cls(
            ticker=ticker,
            price=row.price,
            change=row.change,
            change_percent=row.change_percent,
            day_open=row.day_open,
            day_high=row.day_high,
            day_low=row.day_low,
            previous_close=row.previous_close,
            volume=row.volume,
            quoted_at=row.quoted_at,
            fetched_at=row.fetched_at,
        )


class BarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    is_adjusted: bool


class IndicatorResponse(BaseModel):
    """Null means "not enough history yet", never zero."""

    model_config = ConfigDict(from_attributes=True)

    trade_date: date
    sma_20: Decimal | None = None
    sma_50: Decimal | None = None
    sma_200: Decimal | None = None
    ema_12: Decimal | None = None
    ema_26: Decimal | None = None
    rsi_14: Decimal | None = None
    macd: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_histogram: Decimal | None = None
    atr_14: Decimal | None = None
    volume_sma_20: Decimal | None = None


class SymbolSearchResponse(BaseModel):
    items: list[SymbolResponse]


class QuotesResponse(BaseModel):
    """Keyed by ticker.

    A ticker that was asked for but is absent from the map was either unknown
    or has never been priced; the client renders those as blanks rather than
    treating the whole request as failed.
    """

    quotes: dict[str, QuoteResponse] = Field(default_factory=dict)
