"""Queries against ``latest_quotes``.

Earns its place because reads here are staleness-aware: the caller does not ask
"what is stored", it asks "what is still fresh enough to serve", and that
predicate belongs in one place rather than in every service that wants a price.

Both reads use ``populate_existing``. This table is written by Core ``INSERT
... ON CONFLICT`` statements, which the ORM's identity map knows nothing about,
and the session does not expire on commit — so a row already loaded in this
session would otherwise keep serving its pre-upsert values for the rest of the
request. That bites exactly where it matters: the grid joins in a stale quote,
refreshes it, and then renders the old price anyway.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.market_data.base import Quote
from app.models.market_data import LatestQuote


class QuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_many(self, symbol_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LatestQuote]:
        if not symbol_ids:
            return {}
        result = await self._session.execute(
            select(LatestQuote)
            .where(LatestQuote.symbol_id.in_(symbol_ids))
            .execution_options(populate_existing=True)
        )
        return {row.symbol_id: row for row in result.scalars()}

    async def get_fresh(
        self,
        symbol_ids: Sequence[uuid.UUID],
        *,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> dict[uuid.UUID, LatestQuote]:
        """Only the rows still inside the TTL.

        Measured against ``fetched_at`` — ours — and never ``quoted_at``, which
        is the provider's. A quiet weekend leaves ``quoted_at`` hours old while
        the cache is perfectly fresh; confusing the two turns every Monday
        morning into a stampede of refetches.
        """
        if not symbol_ids:
            return {}
        cutoff = (now or datetime.now(UTC)) - ttl
        result = await self._session.execute(
            select(LatestQuote)
            .where(
                LatestQuote.symbol_id.in_(symbol_ids),
                LatestQuote.fetched_at >= cutoff,
            )
            .execution_options(populate_existing=True)
        )
        return {row.symbol_id: row for row in result.scalars()}

    async def upsert_many(
        self,
        quotes: Mapping[uuid.UUID, Quote],
        *,
        fetched_at: datetime | None = None,
    ) -> int:
        if not quotes:
            return 0

        now = fetched_at or datetime.now(UTC)
        rows = [
            {
                "symbol_id": symbol_id,
                "price": quote.price,
                "change": quote.change,
                "change_percent": quote.change_percent,
                "day_open": quote.day_open,
                "day_high": quote.day_high,
                "day_low": quote.day_low,
                "previous_close": quote.previous_close,
                "volume": quote.volume,
                "quoted_at": quote.quoted_at,
                "fetched_at": now,
            }
            for symbol_id, quote in quotes.items()
        ]

        statement = insert(LatestQuote).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[LatestQuote.symbol_id],
            set_={
                "price": statement.excluded.price,
                "change": statement.excluded.change,
                "change_percent": statement.excluded.change_percent,
                "day_open": statement.excluded.day_open,
                "day_high": statement.excluded.day_high,
                "day_low": statement.excluded.day_low,
                "previous_close": statement.excluded.previous_close,
                "volume": statement.excluded.volume,
                "quoted_at": statement.excluded.quoted_at,
                "fetched_at": statement.excluded.fetched_at,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)
        return len(rows)
