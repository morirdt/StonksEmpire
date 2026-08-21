"""Queries against ``symbols``.

Earns its place twice over: search is a ranked trigram query that no caller
should have to assemble, and the universe seed needs a bulk upsert that is
safe to re-run.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Float, case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.market_data.base import SymbolInfo
from app.models.symbol import Symbol


class SymbolRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, symbol_id: uuid.UUID) -> Symbol | None:
        return await self._session.get(Symbol, symbol_id)

    async def get_by_ticker(self, ticker: str) -> Symbol | None:
        result = await self._session.execute(select(Symbol).where(Symbol.ticker == ticker.upper()))
        return result.scalar_one_or_none()

    async def get_many_by_ticker(self, tickers: Sequence[str]) -> list[Symbol]:
        if not tickers:
            return []
        wanted = [t.upper() for t in tickers]
        result = await self._session.execute(select(Symbol).where(Symbol.ticker.in_(wanted)))
        return list(result.scalars())

    async def search(self, query: str, *, limit: int = 20) -> list[Symbol]:
        """Rank an exact ticker match first, then prefixes, then fuzzy matches.

        Someone typing "AAPL" wants Apple, not the highest-similarity row in a
        table of five hundred names. Similarity alone does not guarantee that,
        so exactness is scored explicitly and used as the primary sort key.
        """
        term = query.strip()
        if not term:
            return []
        upper = term.upper()

        rank = case(
            (Symbol.ticker == upper, 0),
            (Symbol.ticker.startswith(upper), 1),
            (Symbol.name.ilike(f"{term}%"), 2),
            else_=3,
        )
        # The trigram indexes make this a similarity search rather than a scan.
        similarity = func.greatest(
            func.similarity(Symbol.ticker, upper),
            func.similarity(Symbol.name, term),
        ).cast(Float)

        result = await self._session.execute(
            select(Symbol)
            .where(
                Symbol.is_active.is_(True),
                (
                    Symbol.ticker.ilike(f"%{upper}%")
                    | Symbol.name.ilike(f"%{term}%")
                    | (similarity > 0.2)
                ),
            )
            .order_by(rank, similarity.desc(), Symbol.ticker)
            .limit(limit)
        )
        return list(result.scalars())

    async def upsert_many(self, symbols: Sequence[SymbolInfo]) -> int:
        """Insert or refresh the universe. Idempotent — re-running changes nothing.

        ``created_at`` is left alone on conflict so a re-seed does not rewrite
        the history of when a symbol first appeared.
        """
        if not symbols:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "id": uuid.uuid7(),
                "ticker": s.ticker.upper(),
                "name": s.name,
                "exchange": s.exchange,
                "asset_type": s.asset_type.value,
                "currency": s.currency,
                "is_active": True,
                "last_refreshed_at": now,
            }
            for s in symbols
        ]

        statement = insert(Symbol).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[Symbol.ticker],
            set_={
                "name": statement.excluded.name,
                "exchange": statement.excluded.exchange,
                "asset_type": statement.excluded.asset_type,
                "currency": statement.excluded.currency,
                "is_active": statement.excluded.is_active,
                "last_refreshed_at": statement.excluded.last_refreshed_at,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)
        return len(rows)

    async def list_active(self, *, limit: int | None = None) -> list[Symbol]:
        query = select(Symbol).where(Symbol.is_active.is_(True)).order_by(Symbol.ticker)
        if limit is not None:
            query = query.limit(limit)
        result = await self._session.execute(query)
        return list(result.scalars())

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Symbol))
        return result.scalar_one()
