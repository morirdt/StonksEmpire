"""Chart windows and per-user chart preferences.

Two things live here because they are the two halves of "what does this chart
show": the *window* of history, resolved from a query string, and the
*preferences* that seed the controls.

Window resolution is a pure function on purpose. It is the one piece of this
phase with real branching, and it is far cheaper to test against a fixed
``today`` than through an HTTP round trip.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ChartRange
from app.core.exceptions import ValidationError
from app.models.chart_preferences import (
    DEFAULT_OSCILLATORS,
    DEFAULT_OVERLAYS,
    DEFAULT_RANGE,
    UserChartPreferences,
)

#: How far back each range reaches, in calendar days. Calendar rather than
#: trading days because that is what the label promises: "1Y" means a year ago,
#: not 252 bars ago, and the two disagree by a fortnight over holidays.
RANGE_DAYS: dict[ChartRange, int | None] = {
    ChartRange.ONE_MONTH: 30,
    ChartRange.THREE_MONTHS: 91,
    ChartRange.SIX_MONTHS: 182,
    ChartRange.ONE_YEAR: 365,
    ChartRange.TWO_YEARS: 730,
    #: Unbounded — ``MAX_BARS`` is what stops it, not a date.
    ChartRange.MAX: None,
}

#: The widest explicit ``start``/``end`` window the API will serve, in days.
MAX_WINDOW_DAYS = 730

#: Row ceiling on any single response. Two years of daily bars is a little over
#: 500 trading days; 600 leaves enough slack that a ``2Y`` request is never
#: silently truncated, while still keeping ``MAX`` from returning a decade.
#: There is no pagination because there is no case where a chart wants more
#: rows than a screen can plot.
MAX_BARS = 600

#: Used when a request names no window at all. A year is enough history for a
#: 200-day average to have a value across most of the chart, which is the point
#: at which the overlays stop looking broken.
DEFAULT_CHART_RANGE = ChartRange.ONE_YEAR


@dataclass(frozen=True, slots=True)
class ChartWindow:
    """A resolved window. ``start is None`` means "as far back as we hold"."""

    start: date | None
    end: date
    limit: int = MAX_BARS


def resolve_window(
    *,
    chart_range: ChartRange | None = None,
    start: date | None = None,
    end: date | None = None,
    today: date | None = None,
) -> ChartWindow:
    """Turn ``range`` **or** an explicit ``start``/``end`` pair into one window.

    Supplying both is a 422 rather than a silent precedence rule: a client that
    sends ``range=1Y&start=2020-01-01`` has a bug, and quietly honouring one of
    them hides it. An explicit window needs *both* ends for the same reason —
    "from March, to whenever" has more than one defensible meaning.
    """
    today = today or datetime.now(UTC).date()
    has_explicit = start is not None or end is not None

    if chart_range is not None and has_explicit:
        raise ValidationError("Pass either 'range' or an explicit 'start'/'end' window, not both.")

    if has_explicit:
        if start is None or end is None:
            raise ValidationError("An explicit window needs both 'start' and 'end'.")
        if start > end:
            raise ValidationError("'start' must not be after 'end'.")
        if (end - start).days > MAX_WINDOW_DAYS:
            raise ValidationError(
                f"Window too wide: at most {MAX_WINDOW_DAYS} days may be requested at once."
            )
        return ChartWindow(start=start, end=end)

    resolved = chart_range or DEFAULT_CHART_RANGE
    days = RANGE_DAYS[resolved]
    return ChartWindow(start=None if days is None else today - timedelta(days=days), end=today)


class ChartPreferencesService:
    """One row per user, read whole and written whole."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> UserChartPreferences:
        """The caller's row, or an unsaved default instance.

        Returning defaults rather than raising is what stops every client from
        having to special-case "no preferences yet" on first load — and there is
        no meaningful difference between "unset" and "set to the defaults".
        The instance is not added to the session: reading must not write.
        """
        result = await self._session.execute(
            select(UserChartPreferences).where(UserChartPreferences.user_id == user_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        return UserChartPreferences(
            user_id=user_id,
            default_range=DEFAULT_RANGE.value,
            active_overlays=list(DEFAULT_OVERLAYS),
            active_oscillators=list(DEFAULT_OSCILLATORS),
        )

    async def replace(
        self,
        user_id: uuid.UUID,
        *,
        default_range: ChartRange,
        active_overlays: list[str],
        active_oscillators: list[str],
    ) -> UserChartPreferences:
        """Full replacement, never a merge.

        A single upsert rather than get-or-create: the primary key is the user
        id, so two concurrent saves from two tabs cannot produce two rows, and
        the second simply wins.
        """
        statement = insert(UserChartPreferences).values(
            user_id=user_id,
            default_range=default_range.value,
            active_overlays=active_overlays,
            active_oscillators=active_oscillators,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[UserChartPreferences.user_id],
            set_={
                "default_range": statement.excluded.default_range,
                "active_overlays": statement.excluded.active_overlays,
                "active_oscillators": statement.excluded.active_oscillators,
                "updated_at": datetime.now(UTC),
            },
        )
        await self._session.execute(statement)
        await self._session.commit()

        # populate_existing: the upsert above is a Core statement, so the
        # identity map never saw it, and the session does not expire on commit.
        # Without this a row already loaded in this request keeps serving the
        # values it had *before* the write — the Phase 2 quote bug, exactly.
        result = await self._session.execute(
            select(UserChartPreferences)
            .where(UserChartPreferences.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one()
