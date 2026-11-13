"""Running a screen, and saving one.

Two responsibilities that share a module because they share a shape, and are
kept apart inside it: ``run`` builds one query around the compiler's expression,
and the preset methods are ordinary user-scoped CRUD — per ``CLAUDE.md``, no
repository, the session directly.

What the screen reads, and what it deliberately does not:

**One row per symbol, at one shared ``as_of``.** The join is ``symbols``
joined to ``daily_bars`` and ``daily_indicators`` at a single date, and that date is
``max(trade_date)`` across ``daily_bars`` — one date for the whole run, not each
symbol's own latest. Per-symbol latest would quietly compare a symbol that
stopped ingesting three weeks ago against one priced yesterday, and the result
would look like a screen rather than like a bug. A symbol with no bar on
``as_of`` is simply not in the universe for that run, which is what
``universe_size`` exists to make visible.

**``latest_quotes`` is not read.** It is a TTL cache populated as a side effect
of someone viewing a watchlist grid, so screening off it would return whichever
symbols happened to have been looked at recently. History is what ``daily_bars``
is for.

**The indicator join is a LEFT JOIN.** A symbol with bars but no indicator row
should fail an indicator predicate on its merits, not vanish before the
predicate runs — and nulls not matching is the behaviour the whole phase is
built around.

**Only active symbols are ever in the universe.** It is not a filter field: a
screen over delisted names is not something anyone wants, and making it optional
invites getting it wrong.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import FieldUnit, FilterKind, SortDirection
from app.core.exceptions import ConflictError, NotFoundError
from app.models.market_data import DailyBar, DailyIndicator
from app.models.screener import ScreenerPreset
from app.models.symbol import Symbol
from app.schemas.screener import (
    FilterNode,
    ScreenerColumn,
    ScreenerFieldResponse,
    ScreenerFieldsResponse,
    ScreenerRow,
    ScreenerRunResponse,
    ScreenerSort,
    parse_stored_filters,
)
from app.services.screener.compiler import compile_filters, referenced_fields
from app.services.screener.fields import (
    CORE_VALUE_FIELDS,
    DEFAULT_SORT_FIELD,
    FIELDS,
    FieldSpec,
    decimal_or_none,
    resolve,
)

#: The four columns every row carries as identity rather than as a value.
_IDENTITY_FIELDS = frozenset({"ticker", "name", "exchange", "asset_type"})


class ScreenerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------------------------------------------------------------- catalogue

    async def catalogue(self) -> ScreenerFieldsResponse:
        """The field list the filter builder is generated from.

        Served from the same registry the compiler resolves against, so the UI
        cannot offer a field the compiler will reject. A ``category`` field
        whose permitted values are ``None`` gets them from a ``DISTINCT`` over
        the live universe, so the builder offers what actually exists rather
        than what a constant once claimed.
        """
        entries: list[ScreenerFieldResponse] = []
        for spec in FIELDS.values():
            values: list[str] = []
            if spec.supports(FilterKind.CATEGORY):
                values = (
                    list(spec.category_values)
                    if spec.category_values is not None
                    else await self._distinct_values(spec)
                )
            entries.append(
                ScreenerFieldResponse(
                    key=spec.key,
                    label=spec.label,
                    unit=spec.unit,
                    kinds=sorted(spec.kinds, key=lambda kind: kind.value),
                    values=values,
                )
            )

        return ScreenerFieldsResponse(
            fields=entries,
            sort_fields=list(FIELDS),
            default_sort=ScreenerSort(),
        )

    async def _distinct_values(self, spec: FieldSpec) -> list[str]:
        result = await self._session.execute(
            select(spec.expression)
            .distinct()
            .where(Symbol.is_active.is_(True), spec.expression.is_not(None))
            .order_by(spec.expression)
        )
        return [str(value) for value in result.scalars()]

    # ---------------------------------------------------------------- running

    async def run(
        self,
        filters: FilterNode,
        sort: ScreenerSort,
        limit: int,
    ) -> ScreenerRunResponse:
        as_of = await self._as_of()
        if as_of is None:
            # No bars at all: a fresh clone that has not been backfilled. The UI
            # has a distinct empty state for this, because "the universe is
            # empty" would otherwise be diagnosed as "nothing matched".
            return ScreenerRunResponse(
                as_of=None,
                universe_size=0,
                total_matched=0,
                columns=self._columns(filters, sort),
                rows=[],
            )

        predicate = compile_filters(filters)
        universe = await self._session.scalar(
            select(func.count()).select_from(self._universe(as_of).subquery())
        )
        matched = await self._session.scalar(
            select(func.count()).select_from(self._universe(as_of).where(predicate).subquery())
        )

        columns = self._columns(filters, sort)
        value_specs = [resolve(column.key) for column in columns]
        query = (
            self._universe(as_of)
            .where(predicate)
            .with_only_columns(
                Symbol.ticker,
                Symbol.name,
                Symbol.exchange,
                Symbol.asset_type,
                *(spec.expression for spec in value_specs),
            )
            .order_by(*self._ordering(sort))
            .limit(limit)
        )
        result = await self._session.execute(query)

        rows = [
            ScreenerRow(
                ticker=row[0],
                name=row[1],
                exchange=row[2],
                asset_type=row[3],
                values={
                    spec.key: decimal_or_none(value)
                    for spec, value in zip(value_specs, row[4:], strict=True)
                },
            )
            for row in result.all()
        ]

        return ScreenerRunResponse(
            as_of=as_of,
            universe_size=universe or 0,
            total_matched=matched or 0,
            columns=columns,
            rows=rows,
        )

    async def _as_of(self) -> date | None:
        """The one date the whole run is evaluated at."""
        return await self._session.scalar(select(func.max(DailyBar.trade_date)))

    def _universe(self, as_of: date) -> Select[Any]:
        """Active symbols that have a bar on ``as_of``, with indicators attached.

        The bar join is inner and carries the date in its ``ON`` clause, which
        is what makes "has a bar on this date" the definition of the universe.
        The indicator join is outer, so a missing indicator row is a null to be
        failed by a predicate rather than a symbol that disappears before the
        predicate runs.
        """
        return (
            # A column is named here only so the query is legal on its own; the
            # row query replaces it with what it actually wants, and the counts
            # wrap it in a subquery.
            select(Symbol.id)
            .join(
                DailyBar,
                and_(DailyBar.symbol_id == Symbol.id, DailyBar.trade_date == as_of),
            )
            .outerjoin(
                DailyIndicator,
                and_(DailyIndicator.symbol_id == Symbol.id, DailyIndicator.trade_date == as_of),
            )
            .where(Symbol.is_active.is_(True))
        )

    def _columns(self, filters: FilterNode, sort: ScreenerSort) -> list[ScreenerColumn]:
        """The fixed core, then every field the run referenced.

        So the results table shows the numbers that caused the match and a user
        can see *why* a symbol is there without opening it. Identity fields are
        skipped because every row already carries them.
        """
        keys = [*CORE_VALUE_FIELDS, *referenced_fields(filters), sort.field]
        ordered: dict[str, None] = {}
        for key in keys:
            # Identity fields are already on every row, and a text field has no
            # place in a map of Decimals — which is what `values` is declared
            # as, so this is what keeps that declaration true.
            if key not in _IDENTITY_FIELDS and FIELDS[key].unit is not FieldUnit.TEXT:
                ordered.setdefault(key, None)
        return [
            ScreenerColumn(key=key, label=FIELDS[key].label, unit=FIELDS[key].unit)
            for key in ordered
        ]

    def _ordering(self, sort: ScreenerSort) -> list[Any]:
        """Sort, nulls last in both directions, tie-broken by ticker.

        Nulls last rather than Postgres's default, which puts them first under
        ``DESC``: a symbol with no value for the sorted field is not the best
        match, and showing it at the top reads as one. The ticker tiebreak makes
        the order total, so re-running an unchanged screen returns the same rows
        in the same sequence.
        """
        column = resolve(sort.field).expression
        primary = column.desc() if sort.direction is SortDirection.DESC else column.asc()
        ordering: list[Any] = [primary.nulls_last()]
        if sort.field != DEFAULT_SORT_FIELD:
            ordering.append(Symbol.ticker.asc())
        return ordering

    # ---------------------------------------------------------------- presets

    async def list_presets(self, user_id: uuid.UUID) -> list[ScreenerPreset]:
        """Newest first, and **without** parsing the stored trees.

        A preset whose tree no longer validates must not stop the list page
        loading; it fails when it is opened or run, which is where a user can
        do something about it.

        The id breaks ties, and can: primary keys here are UUIDv7, so they sort
        in creation order. ``created_at`` alone is not enough — its default is
        ``now()``, which is *transaction* time, so two presets created inside
        one transaction share a timestamp exactly and come back in whatever
        order the heap happens to hold them.
        """
        result = await self._session.execute(
            select(ScreenerPreset)
            .where(ScreenerPreset.user_id == user_id)
            .order_by(ScreenerPreset.created_at.desc(), ScreenerPreset.id.desc())
        )
        return list(result.scalars())

    async def get_preset(self, user_id: uuid.UUID, preset_id: uuid.UUID) -> ScreenerPreset:
        """One preset, or 404 — including when it belongs to someone else."""
        result = await self._session.execute(
            select(ScreenerPreset).where(
                ScreenerPreset.id == preset_id,
                ScreenerPreset.user_id == user_id,
            )
        )
        preset = result.scalar_one_or_none()
        if preset is None:
            raise NotFoundError("Screener preset not found.")
        return preset

    async def create_preset(
        self,
        user_id: uuid.UUID,
        *,
        name: str,
        filters: FilterNode,
        sort: ScreenerSort,
    ) -> ScreenerPreset:
        preset = ScreenerPreset(
            user_id=user_id,
            name=name.strip(),
            filters=_dump(filters),
            sort_field=sort.field,
            sort_direction=sort.direction.value,
        )
        self._session.add(preset)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(f"A screen named {name!r} already exists.") from exc

        await self._session.refresh(preset)
        return preset

    async def update_preset(
        self,
        user_id: uuid.UUID,
        preset_id: uuid.UUID,
        *,
        name: str | None = None,
        filters: FilterNode | None = None,
        sort: ScreenerSort | None = None,
    ) -> ScreenerPreset:
        preset = await self.get_preset(user_id, preset_id)

        if name is not None:
            preset.name = name.strip()
        if filters is not None:
            preset.filters = _dump(filters)
        if sort is not None:
            preset.sort_field = sort.field
            preset.sort_direction = sort.direction.value

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError(f"A screen named {name!r} already exists.") from exc

        await self._session.refresh(preset)
        return preset

    async def delete_preset(self, user_id: uuid.UUID, preset_id: uuid.UUID) -> None:
        preset = await self.get_preset(user_id, preset_id)
        await self._session.delete(preset)
        await self._session.commit()

    async def run_preset(
        self,
        user_id: uuid.UUID,
        preset_id: uuid.UUID,
        *,
        limit: int,
    ) -> ScreenerRunResponse:
        """Run what is saved, rather than what a client says is saved.

        This endpoint exists instead of making the client fetch-then-post: it is
        the primary path from the UI, it halves the round trips, and it removes
        the window in which a client can run something subtly different from
        what is stored under that name.
        """
        preset = await self.get_preset(user_id, preset_id)
        return await self.run(
            parse_stored_filters(preset.filters),
            preset_sort(preset),
            limit,
        )


def _dump(filters: FilterNode) -> dict[str, Any]:
    """The tree as plain JSON-able data, straight from the validated model."""
    return filters.model_dump(mode="json")


def preset_sort(preset: ScreenerPreset) -> ScreenerSort:
    """The stored sort, falling back to the default if its field has gone.

    Unlike the filter tree, a sort that no longer resolves is not worth failing
    a run over — the screen is still meaningful, it just comes back in the
    default order.
    """
    field = preset.sort_field if preset.sort_field in FIELDS else DEFAULT_SORT_FIELD
    return ScreenerSort(field=field, direction=SortDirection(preset.sort_direction))


def preset_filters(preset: ScreenerPreset) -> FilterNode:
    """The stored tree, re-validated. Raises a 422 if it has gone stale."""
    return parse_stored_filters(preset.filters)


__all__ = ["ScreenerService", "preset_filters", "preset_sort"]
