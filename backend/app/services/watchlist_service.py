"""Watchlist CRUD, scoped to one user.

Ordinary enough that ``CLAUDE.md`` says no repository: this uses the session
directly. What is *not* ordinary is the scoping. Every read here filters by
``user_id``, and a row belonging to someone else comes back as **404, not 403** —
a 403 confirms the row exists, which hands an attacker an enumeration oracle for
free. The parameterized harness in ``tests/integration/test_cross_user_authorization.py``
exists to prove this holds for every endpoint.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.market_data import LatestQuote
from app.models.symbol import Symbol
from app.models.watchlist import Watchlist, WatchlistItem


class WatchlistService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --------------------------------------------------------------- watchlists

    async def list_for_user(self, user_id: uuid.UUID) -> list[Watchlist]:
        result = await self._session.execute(
            select(Watchlist)
            .where(Watchlist.user_id == user_id)
            .order_by(Watchlist.is_default.desc(), Watchlist.created_at)
        )
        return list(result.scalars())

    async def get_for_user(self, user_id: uuid.UUID, watchlist_id: uuid.UUID) -> Watchlist:
        """Fetch one list, or 404 — including when it belongs to someone else."""
        result = await self._session.execute(
            select(Watchlist).where(
                Watchlist.id == watchlist_id,
                Watchlist.user_id == user_id,
            )
        )
        watchlist = result.scalar_one_or_none()
        if watchlist is None:
            raise NotFoundError("Watchlist not found.")
        return watchlist

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        name: str,
        is_default: bool = False,
    ) -> Watchlist:
        watchlist = Watchlist(user_id=user_id, name=name.strip(), is_default=is_default)
        if is_default:
            await self._clear_default(user_id)

        self._session.add(watchlist)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(f"A watchlist named {name!r} already exists.") from exc

        await self._session.refresh(watchlist)
        return watchlist

    async def update(
        self,
        user_id: uuid.UUID,
        watchlist_id: uuid.UUID,
        *,
        name: str | None = None,
        is_default: bool | None = None,
    ) -> Watchlist:
        watchlist = await self.get_for_user(user_id, watchlist_id)

        if name is not None:
            watchlist.name = name.strip()
        if is_default is not None:
            if is_default:
                await self._clear_default(user_id, except_id=watchlist.id)
            watchlist.is_default = is_default

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(f"A watchlist named {name!r} already exists.") from exc

        await self._session.refresh(watchlist)
        return watchlist

    async def delete(self, user_id: uuid.UUID, watchlist_id: uuid.UUID) -> None:
        watchlist = await self.get_for_user(user_id, watchlist_id)
        await self._session.delete(watchlist)
        await self._session.commit()

    async def _clear_default(
        self,
        user_id: uuid.UUID,
        *,
        except_id: uuid.UUID | None = None,
    ) -> None:
        """At most one default per user. Promoting one demotes the rest."""
        lists = await self.list_for_user(user_id)
        for other in lists:
            if other.is_default and other.id != except_id:
                other.is_default = False

    # --------------------------------------------------------------------- items

    async def add_item(
        self,
        user_id: uuid.UUID,
        watchlist_id: uuid.UUID,
        *,
        ticker: str,
        notes: str | None = None,
    ) -> WatchlistItem:
        watchlist = await self.get_for_user(user_id, watchlist_id)

        result = await self._session.execute(select(Symbol).where(Symbol.ticker == ticker.upper()))
        symbol = result.scalar_one_or_none()
        if symbol is None:
            raise NotFoundError(f"Unknown ticker {ticker.upper()!r}.")

        # Read the ticker before the commit: a rollback expires every loaded
        # object, so touching an attribute afterwards attempts lazy IO from a
        # context that cannot await it.
        resolved_ticker = symbol.ticker

        next_position = await self._next_position(watchlist.id)
        item = WatchlistItem(
            watchlist_id=watchlist.id,
            symbol_id=symbol.id,
            position=next_position,
            notes=notes,
        )
        self._session.add(item)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(f"{resolved_ticker} is already on this watchlist.") from exc

        await self._session.refresh(item)
        return item

    async def remove_item(
        self,
        user_id: uuid.UUID,
        watchlist_id: uuid.UUID,
        item_id: uuid.UUID,
    ) -> None:
        watchlist = await self.get_for_user(user_id, watchlist_id)
        result = await self._session.execute(
            select(WatchlistItem).where(
                WatchlistItem.id == item_id,
                WatchlistItem.watchlist_id == watchlist.id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise NotFoundError("Watchlist item not found.")

        await self._session.delete(item)
        await self._session.commit()
        await self._compact_positions(watchlist.id)

    async def reorder(
        self,
        user_id: uuid.UUID,
        watchlist_id: uuid.UUID,
        item_ids: Sequence[uuid.UUID],
    ) -> list[WatchlistItem]:
        """Set the order of every item at once.

        The whole list is required rather than a moved pair: a partial reorder
        has no well-defined answer for the items it omits, and drag-and-drop
        already knows the full order it wants.
        """
        watchlist = await self.get_for_user(user_id, watchlist_id)
        items = await self.list_items(user_id, watchlist.id)

        known = {item.id for item in items}
        requested = list(item_ids)
        if set(requested) != known or len(requested) != len(known):
            raise ValidationError("The reorder must list every item on the watchlist exactly once.")

        by_id = {item.id: item for item in items}
        for position, item_id in enumerate(requested):
            by_id[item_id].position = position

        await self._session.commit()
        return await self.list_items(user_id, watchlist.id)

    async def list_items(self, user_id: uuid.UUID, watchlist_id: uuid.UUID) -> list[WatchlistItem]:
        watchlist = await self.get_for_user(user_id, watchlist_id)
        result = await self._session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist.id)
            .order_by(WatchlistItem.position, WatchlistItem.created_at)
        )
        return list(result.scalars())

    async def list_rows(
        self,
        user_id: uuid.UUID,
        watchlist_id: uuid.UUID,
    ) -> list[tuple[WatchlistItem, Symbol, LatestQuote | None]]:
        """The grid payload, authorizing the watchlist first."""
        watchlist = await self.get_for_user(user_id, watchlist_id)
        return await self.rows_for(watchlist)

    async def rows_for(
        self,
        watchlist: Watchlist,
    ) -> list[tuple[WatchlistItem, Symbol, LatestQuote | None]]:
        """Items joined to symbols and quotes in one query.

        This is what the frontend polls, so it must not be N+1. The quote join
        is outer — a symbol with no quote yet is a row with blank prices, not a
        missing row.

        Takes the ``Watchlist`` itself rather than an id, so it cannot be
        reached without having gone through an authorizing lookup first.
        """
        result = await self._session.execute(
            select(WatchlistItem, Symbol, LatestQuote)
            .join(Symbol, Symbol.id == WatchlistItem.symbol_id)
            .outerjoin(LatestQuote, LatestQuote.symbol_id == WatchlistItem.symbol_id)
            .where(WatchlistItem.watchlist_id == watchlist.id)
            .order_by(WatchlistItem.position, WatchlistItem.created_at)
        )
        return [(item, symbol, quote) for item, symbol, quote in result.all()]

    async def _next_position(self, watchlist_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.max(WatchlistItem.position)).where(
                WatchlistItem.watchlist_id == watchlist_id
            )
        )
        highest = result.scalar_one()
        return 0 if highest is None else highest + 1

    async def _compact_positions(self, watchlist_id: uuid.UUID) -> None:
        """Keep positions dense after a removal, so ordering stays predictable."""
        result = await self._session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.position, WatchlistItem.created_at)
        )
        for position, item in enumerate(result.scalars()):
            item.position = position
        await self._session.commit()
