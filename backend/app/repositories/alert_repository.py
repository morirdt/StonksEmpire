"""The alert evaluation queries. The one repository the alerts feature has.

Alert CRUD is ordinary user-scoped CRUD and uses the session directly, per
``CLAUDE.md``. These queries are not CRUD: they put a compiled filter tree in a
``WHERE`` against the right source at the right instant, and they are shared by
the worker's jobs and by the create endpoint's ``condition_met_now``.

**Evaluation runs in SQL through the screener's compiler.** One alert's
condition comes back as ``TRUE``, ``FALSE``, or ``NULL``, using the same
expression and the same null semantics as a screen. There is no second
evaluator in Python, because two evaluators would mean two sets of null rules.
One statement per alert is fine at this scale.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AlertKind, AlertStatus, FieldUnit
from app.models.alert import Alert
from app.models.market_data import DailyIndicator, LatestQuote
from app.models.symbol import Symbol
from app.schemas.screener import FilterNode, tree_fields
from app.services.screener.compiler import compile_filters
from app.services.screener.fields import (
    FIELDS,
    INTRADAY_FIELDS,
    FieldRegistry,
    decimal_or_none,
)
from app.services.screener.service import universe_select

__all__ = ["AlertRepository", "Evaluation"]

type Evaluation = tuple[bool | None, dict[str, str | None]]


def _snapshot_keys(tree: FilterNode, core: str, registry: FieldRegistry) -> list[str]:
    """The core price field, then every number the condition named."""
    keys: dict[str, None] = {core: None}
    for key in tree_fields(tree):
        if registry[key].unit is not FieldUnit.TEXT:
            keys.setdefault(key, None)
    return list(keys)


def _as_text(value: Any) -> str | None:
    number: Decimal | None = decimal_or_none(value)
    return None if number is None else str(number)


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------- selection

    async def active_ids(
        self,
        kind: AlertKind,
        *,
        symbol_ids: Sequence[uuid.UUID] | None = None,
    ) -> list[uuid.UUID]:
        """Active alerts of one kind, optionally only on some symbols. Oldest first."""
        query = select(Alert.id).where(Alert.kind == kind.value, Alert.status == "active")
        if symbol_ids is not None:
            if not symbol_ids:
                return []
            query = query.where(Alert.symbol_id.in_(symbol_ids))
        result = await self._session.execute(query.order_by(Alert.id))
        return list(result.scalars())

    async def live_ids_on_symbols(self, symbol_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
        """Every alert on these symbols that is not already disabled."""
        if not symbol_ids:
            return []
        result = await self._session.execute(
            select(Alert.id)
            .where(
                Alert.symbol_id.in_(symbol_ids),
                Alert.status != AlertStatus.DISABLED.value,
            )
            .order_by(Alert.id)
        )
        return list(result.scalars())

    # ------------------------------------------------------------ evaluation

    async def _evaluate(
        self,
        base: Select[Any],
        tree: FilterNode,
        registry: FieldRegistry,
        core: str,
    ) -> Evaluation:
        keys = _snapshot_keys(tree, core, registry)
        query = base.with_only_columns(
            compile_filters(tree, registry).label("state"),
            *(registry[key].expression for key in keys),
        )
        row = (await self._session.execute(query)).first()
        if row is None:
            # No row is no data: the symbol has no bar on as_of, or no quote
            # from this run. That is NULL, which never creates an edge.
            return None, {}
        state: bool | None = row[0]
        return state, {key: _as_text(value) for key, value in zip(keys, row[1:], strict=True)}

    async def evaluate_end_of_day(
        self, tree: FilterNode, symbol_id: uuid.UUID, as_of: date
    ) -> Evaluation:
        """One symbol against the screener's universe at the shared ``as_of``."""
        base = universe_select(as_of).where(Symbol.id == symbol_id)
        return await self._evaluate(base, tree, FIELDS, "close")

    async def evaluate_intraday(
        self,
        tree: FilterNode,
        symbol_id: uuid.UUID,
        *,
        fetched_since: datetime | None,
        indicators_as_of: date | None,
    ) -> Evaluation:
        """One symbol's live quote, with last night's indicators beside it.

        ``fetched_since`` is the poll run's start: **only quotes this run
        actually fetched are evaluated**, never the table as a whole. During a
        provider outage the grid serves stale quotes with an honest timestamp,
        and that is right for a grid. Evaluating one here would fire an alert on
        a price the provider has not confirmed. ``None`` accepts any quote,
        which is only for ``condition_met_now`` on create.
        """
        quote_join = LatestQuote.symbol_id == Symbol.id
        if fetched_since is not None:
            quote_join = and_(quote_join, LatestQuote.fetched_at >= fetched_since)
        indicator_join = (
            and_(
                DailyIndicator.symbol_id == Symbol.id,
                DailyIndicator.trade_date == indicators_as_of,
            )
            if indicators_as_of is not None
            else false()
        )
        base = (
            select(Symbol.id)
            .join(LatestQuote, quote_join)
            .outerjoin(DailyIndicator, indicator_join)
            .where(Symbol.id == symbol_id, Symbol.is_active.is_(True))
        )
        return await self._evaluate(base, tree, INTRADAY_FIELDS, "price")

    async def screen_matches(self, tree: FilterNode, as_of: date) -> list[tuple[uuid.UUID, str]]:
        """Every symbol matching a preset's tree at ``as_of``: no cap, ticker order."""
        query = (
            universe_select(as_of)
            .where(compile_filters(tree, FIELDS))
            .with_only_columns(Symbol.id, Symbol.ticker)
            .order_by(Symbol.ticker)
        )
        result = await self._session.execute(query)
        return [(symbol_id, ticker) for symbol_id, ticker in result.tuples()]
