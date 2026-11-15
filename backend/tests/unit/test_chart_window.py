"""Chart window resolution.

Pure, so it is tested against a fixed ``today`` rather than through HTTP. This
is the only real branching the phase's backend has, and every branch below is
something a client can reach with a hand-written query string.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.enums import ChartRange
from app.core.exceptions import ValidationError
from app.services.chart_service import (
    DEFAULT_CHART_RANGE,
    MAX_BARS,
    MAX_WINDOW_DAYS,
    RANGE_DAYS,
    resolve_window,
)

TODAY = date(2026, 8, 21)


@pytest.mark.parametrize(
    ("chart_range", "expected_days"),
    [
        (ChartRange.ONE_MONTH, 30),
        (ChartRange.THREE_MONTHS, 91),
        (ChartRange.SIX_MONTHS, 182),
        (ChartRange.ONE_YEAR, 365),
        (ChartRange.TWO_YEARS, 730),
    ],
)
def test_each_range_maps_to_the_window_it_claims(
    chart_range: ChartRange,
    expected_days: int,
) -> None:
    window = resolve_window(chart_range=chart_range, today=TODAY)

    assert window.end == TODAY
    assert window.start == TODAY - timedelta(days=expected_days)


def test_max_has_no_start_date() -> None:
    """MAX means "everything stored"; the row cap is what bounds it, not a date."""
    window = resolve_window(chart_range=ChartRange.MAX, today=TODAY)

    assert window.start is None
    assert window.end == TODAY
    assert window.limit == MAX_BARS


def test_every_range_is_resolvable() -> None:
    """A new enum member without a RANGE_DAYS entry would be a KeyError in prod."""
    assert set(RANGE_DAYS) == set(ChartRange)


def test_no_arguments_falls_back_to_the_default_range() -> None:
    assert resolve_window(today=TODAY) == resolve_window(
        chart_range=DEFAULT_CHART_RANGE, today=TODAY
    )


def test_an_explicit_window_is_taken_as_given() -> None:
    window = resolve_window(start=date(2026, 1, 1), end=date(2026, 3, 1), today=TODAY)

    assert window.start == date(2026, 1, 1)
    assert window.end == date(2026, 3, 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chart_range": ChartRange.ONE_YEAR, "start": date(2026, 1, 1)},
        {"chart_range": ChartRange.ONE_YEAR, "end": date(2026, 3, 1)},
        {
            "chart_range": ChartRange.MAX,
            "start": date(2026, 1, 1),
            "end": date(2026, 3, 1),
        },
    ],
)
def test_a_range_together_with_explicit_dates_is_rejected(kwargs: dict[str, object]) -> None:
    """No silent precedence rule: a client sending both has a bug worth surfacing."""
    with pytest.raises(ValidationError):
        resolve_window(today=TODAY, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [{"start": date(2026, 1, 1)}, {"end": date(2026, 3, 1)}],
)
def test_half_a_window_is_rejected(kwargs: dict[str, date]) -> None:
    """ "From March, to whenever" has more than one defensible meaning."""
    with pytest.raises(ValidationError):
        resolve_window(today=TODAY, **kwargs)


def test_a_backwards_window_is_rejected() -> None:
    with pytest.raises(ValidationError):
        resolve_window(start=date(2026, 3, 1), end=date(2026, 1, 1), today=TODAY)


def test_a_window_wider_than_the_cap_is_rejected() -> None:
    end = TODAY
    with pytest.raises(ValidationError):
        resolve_window(start=end - timedelta(days=MAX_WINDOW_DAYS + 1), end=end, today=TODAY)


def test_a_window_exactly_at_the_cap_is_allowed() -> None:
    """Off-by-one here would make the 2Y preset unrequestable as explicit dates."""
    end = TODAY
    window = resolve_window(start=end - timedelta(days=MAX_WINDOW_DAYS), end=end, today=TODAY)

    assert window.start == end - timedelta(days=MAX_WINDOW_DAYS)


def test_the_two_year_preset_fits_under_the_row_cap() -> None:
    """MAX_BARS must not silently truncate the widest preset.

    Two years is ~505 trading days; if the cap ever drops below that, 2Y starts
    quietly returning a shorter chart than its label promises.
    """
    trading_days = RANGE_DAYS[ChartRange.TWO_YEARS]
    assert trading_days is not None
    assert trading_days * 5 / 7 < MAX_BARS
