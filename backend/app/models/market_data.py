"""Prices: daily bars, the indicators derived from them, and current quotes.

Three tables with three different shapes, for three different reasons.

``daily_bars`` and ``daily_indicators`` are keyed ``(symbol_id, trade_date)`` —
the one place in this project where a natural key beats a surrogate. It makes
the idempotent upsert every backfill re-run depends on trivial, and it is the
index the range queries want anyway.

``latest_quotes`` is keyed by ``symbol_id`` alone, because it is a cache of
current state rather than a history. History is what ``daily_bars`` is for.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Numeric, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: Prices and quantities. NUMERIC, never float — see CLAUDE.md.
_PRICE = Numeric(18, 6)


class DailyBar(Base, TimestampMixin):
    __tablename__ = "daily_bars"
    #: The primary key leads with ``symbol_id``, which is the right order for
    #: every per-symbol query. Phase 4's screener is the exception: it filters
    #: on ``trade_date`` first and ``symbol_id`` not at all, which that index
    #: cannot serve. Hence a second index on the date alone.
    __table_args__ = (Index("ix_daily_bars_trade_date", "trade_date"),)

    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("symbols.id", ondelete="CASCADE"),
        primary_key=True,
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)

    open: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Whether the provider gave us split- and dividend-adjusted prices. Stored
    #: rather than assumed, so a later backfill can tell which rows to replace.
    is_adjusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<DailyBar {self.symbol_id} {self.trade_date} {self.close}>"


class DailyIndicator(Base, TimestampMixin):
    """One column per indicator, all nullable.

    Wide rather than a tall ``(name, value)`` table because Phase 4's screener
    filters on several indicators at once and compiles to SQL; tall storage
    would turn every screen into a pile of self-joins and make indexing
    hopeless. The cost is a migration per new indicator, which is the right
    trade at this scale.

    Every column is nullable because the leading rows of any window have no
    value — a 200-day average needs 200 days first.

    **These are computed here, never fetched.** Providers disagree with each
    other on RSI smoothing and on what "MACD" means; computing locally is what
    stops the Phase 3 chart and the Phase 4 screen from contradicting each
    other over the same symbol.
    """

    __tablename__ = "daily_indicators"
    #: See ``DailyBar`` — the screener joins this table on the date too.
    __table_args__ = (Index("ix_daily_indicators_trade_date", "trade_date"),)

    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("symbols.id", ondelete="CASCADE"),
        primary_key=True,
    )
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)

    sma_20: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    sma_50: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    sma_200: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    ema_12: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    ema_26: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    rsi_14: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    macd: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    macd_signal: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    macd_histogram: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    atr_14: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    volume_sma_20: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    #: Percent change from the previous bar's close. Null on a symbol's first
    #: bar, where there is no previous close to change from.
    change_percent_1d: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    #: Trailing 52-week extremes, counted in **bars, not calendar days** — 252
    #: is a trading year. ``ChartRange`` is calendar-based instead, because a
    #: toolbar label promises calendar time; both are right for their own job.
    high_52w: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    low_52w: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)

    def __repr__(self) -> str:
        return f"<DailyIndicator {self.symbol_id} {self.trade_date}>"


class LatestQuote(Base, TimestampMixin):
    """The most recent price seen for a symbol.

    Two timestamps, because they answer different questions. ``quoted_at`` is
    the provider's — how old is this price. ``fetched_at`` is ours, and it is
    what the cache TTL is measured against. Conflating them makes a quiet
    weekend look identical to a broken ingest.
    """

    __tablename__ = "latest_quotes"

    symbol_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("symbols.id", ondelete="CASCADE"),
        primary_key=True,
    )

    price: Mapped[Decimal] = mapped_column(_PRICE, nullable=False)
    change: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    change_percent: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    day_open: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    day_high: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    day_low: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    quoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<LatestQuote {self.symbol_id} {self.price}>"
