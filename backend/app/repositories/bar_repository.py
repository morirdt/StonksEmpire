"""Queries against ``daily_bars`` and ``daily_indicators``.

Both tables are keyed ``(symbol_id, trade_date)``, which is what makes the bulk
upsert here a single statement and makes re-running a backfill a no-op rather
than a duplicate-key error.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.market_data.base import Bar
from app.models.market_data import DailyBar, DailyIndicator
from app.services.indicator_service import IndicatorSet

#: Indicator columns, in one place: the upsert, the model, and the migration all
#: have to agree, and Phase 4 screens against exactly this list.
_INDICATOR_COLUMNS = (
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_12",
    "ema_26",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "atr_14",
    "volume_sma_20",
)


class BarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --------------------------------------------------------------------- bars

    async def get_range(
        self,
        symbol_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
    ) -> list[DailyBar]:
        """Bars for one symbol, oldest first.

        The ordering is not incidental: ``compute_indicators`` is positional and
        would silently produce nonsense from an unsorted series.
        """
        query = select(DailyBar).where(DailyBar.symbol_id == symbol_id)
        if start is not None:
            query = query.where(DailyBar.trade_date >= start)
        if end is not None:
            query = query.where(DailyBar.trade_date <= end)

        if limit is not None:
            # Take the most recent N, then hand them back oldest-first.
            query = query.order_by(DailyBar.trade_date.desc()).limit(limit)
            result = await self._session.execute(query)
            return sorted(result.scalars(), key=lambda b: b.trade_date)

        result = await self._session.execute(query.order_by(DailyBar.trade_date))
        return list(result.scalars())

    async def as_provider_bars(
        self,
        symbol_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        """Stored bars in the provider dataclass shape.

        Indicator computation takes ``Bar`` whatever the source, so recomputing
        from the database and computing from a fresh fetch run identical code.
        """
        return [
            Bar(
                trade_date=row.trade_date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                is_adjusted=row.is_adjusted,
            )
            for row in await self.get_range(symbol_id, start=start, end=end)
        ]

    async def upsert_bars(self, symbol_id: uuid.UUID, bars: Sequence[Bar]) -> int:
        """Insert or replace bars. Re-running a backfill must change nothing."""
        if not bars:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "symbol_id": symbol_id,
                "trade_date": bar.trade_date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "is_adjusted": bar.is_adjusted,
            }
            for bar in bars
        ]

        statement = insert(DailyBar).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[DailyBar.symbol_id, DailyBar.trade_date],
            set_={
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume": statement.excluded.volume,
                "is_adjusted": statement.excluded.is_adjusted,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)
        return len(rows)

    async def earliest_and_latest(self, symbol_id: uuid.UUID) -> tuple[date | None, date | None]:
        result = await self._session.execute(
            select(func.min(DailyBar.trade_date), func.max(DailyBar.trade_date)).where(
                DailyBar.symbol_id == symbol_id
            )
        )
        first, last = result.one()
        return first, last

    async def count_for(self, symbol_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(DailyBar).where(DailyBar.symbol_id == symbol_id)
        )
        return result.scalar_one()

    # --------------------------------------------------------------- indicators

    async def upsert_indicators(
        self,
        symbol_id: uuid.UUID,
        indicators: Sequence[IndicatorSet],
    ) -> int:
        if not indicators:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "symbol_id": symbol_id,
                "trade_date": row.trade_date,
                **{column: getattr(row, column) for column in _INDICATOR_COLUMNS},
            }
            for row in indicators
        ]

        statement = insert(DailyIndicator).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[DailyIndicator.symbol_id, DailyIndicator.trade_date],
            set_={
                **{column: getattr(statement.excluded, column) for column in _INDICATOR_COLUMNS},
                "updated_at": now,
            },
        )
        await self._session.execute(statement)
        return len(rows)

    async def get_indicators(
        self,
        symbol_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
    ) -> list[DailyIndicator]:
        """Indicator rows for one symbol, oldest first.

        ``limit`` takes the most recent N and mirrors ``get_range`` exactly.
        That symmetry is load-bearing: the chart aligns bars and indicators
        positionally, so the two queries have to truncate from the same end.
        """
        query = select(DailyIndicator).where(DailyIndicator.symbol_id == symbol_id)
        if start is not None:
            query = query.where(DailyIndicator.trade_date >= start)
        if end is not None:
            query = query.where(DailyIndicator.trade_date <= end)

        if limit is not None:
            query = query.order_by(DailyIndicator.trade_date.desc()).limit(limit)
            result = await self._session.execute(query)
            return sorted(result.scalars(), key=lambda row: row.trade_date)

        result = await self._session.execute(query.order_by(DailyIndicator.trade_date))
        return list(result.scalars())

    async def delete_indicators(self, symbol_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DailyIndicator).where(DailyIndicator.symbol_id == symbol_id)
        )
