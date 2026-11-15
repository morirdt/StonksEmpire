"""Market hours, holidays, and DST, checked with frozen instants.

Every instant is written in ET and converted, because that is how the rules are
stated. The DST cases then check the UTC result, which is the conversion that
goes wrong if an offset is ever hard-coded.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.core.market_calendar import (
    MARKET_TZ,
    is_market_open,
    is_trading_day,
    last_completed_session,
    session_bounds,
)


def et(*args: int) -> datetime:
    """An ET wall-clock instant, as an aware datetime in UTC."""
    return datetime(*args, tzinfo=MARKET_TZ).astimezone(UTC)  # type: ignore[misc]


# A plain Wednesday, after the November DST change.
WEDNESDAY = date(2026, 11, 18)


@pytest.mark.parametrize(
    ("instant", "expected"),
    [
        (et(2026, 11, 18, 9, 29, 59), False),
        (et(2026, 11, 18, 9, 30, 0), True),
        (et(2026, 11, 18, 15, 59, 59), True),
        (et(2026, 11, 18, 16, 0, 0), False),
    ],
    ids=["09:29:59", "09:30:00", "15:59:59", "16:00:00"],
)
def test_the_session_boundaries(instant: datetime, expected: bool) -> None:
    assert is_market_open(instant) is expected


def test_a_weekend_is_not_a_trading_day() -> None:
    saturday = date(2026, 11, 14)

    assert not is_trading_day(saturday)
    assert session_bounds(saturday) is None
    assert not is_market_open(et(2026, 11, 14, 12, 0))


def test_a_holiday_is_not_a_trading_day() -> None:
    thanksgiving = date(2026, 11, 26)

    assert thanksgiving.weekday() == 3, "a weekday, so only the holiday calendar excludes it"
    assert not is_trading_day(thanksgiving)
    assert not is_market_open(et(2026, 11, 26, 12, 0))


def test_good_friday_is_closed() -> None:
    """Not a federal holiday, so a generic US calendar would get this wrong."""
    assert not is_trading_day(date(2026, 4, 3))


def test_an_observed_holiday_is_closed() -> None:
    """July 4th 2026 is a Saturday, so the market closes on Friday the 3rd."""
    assert not is_trading_day(date(2026, 7, 3))


def test_an_ordinary_weekday_is_a_trading_day() -> None:
    assert is_trading_day(WEDNESDAY)


@pytest.mark.parametrize(
    ("day", "open_utc", "close_utc"),
    [
        # Friday before the spring change: still EST, UTC-5.
        (
            date(2026, 3, 6),
            datetime(2026, 3, 6, 14, 30, tzinfo=UTC),
            datetime(2026, 3, 6, 21, 0, tzinfo=UTC),
        ),
        # Monday after it (clocks went forward on Sunday 8 March): EDT, UTC-4.
        (
            date(2026, 3, 9),
            datetime(2026, 3, 9, 13, 30, tzinfo=UTC),
            datetime(2026, 3, 9, 20, 0, tzinfo=UTC),
        ),
        # Friday before the autumn change: EDT.
        (
            date(2026, 10, 30),
            datetime(2026, 10, 30, 13, 30, tzinfo=UTC),
            datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
        ),
        # Monday after it (clocks went back on Sunday 1 November): EST again.
        (
            date(2026, 11, 2),
            datetime(2026, 11, 2, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 2, 21, 0, tzinfo=UTC),
        ),
    ],
    ids=["before-spring", "after-spring", "before-autumn", "after-autumn"],
)
def test_session_bounds_follow_both_dst_transitions(
    day: date, open_utc: datetime, close_utc: datetime
) -> None:
    assert session_bounds(day) == (open_utc, close_utc)


def test_the_same_utc_instant_is_open_in_summer_and_closed_in_winter() -> None:
    """13:45 UTC is 09:45 EDT but 08:45 EST. A fixed offset gets one of them wrong."""
    assert is_market_open(datetime(2026, 10, 30, 13, 45, tzinfo=UTC))
    assert not is_market_open(datetime(2026, 11, 2, 13, 45, tzinfo=UTC))


def test_the_last_completed_session_turns_over_at_the_close() -> None:
    assert last_completed_session(et(2026, 11, 18, 15, 59)) == date(2026, 11, 17)
    assert last_completed_session(et(2026, 11, 18, 16, 0)) == WEDNESDAY


def test_the_last_completed_session_skips_weekends_and_holidays() -> None:
    # Before Monday's open: Friday.
    assert last_completed_session(et(2026, 11, 16, 9, 0)) == date(2026, 11, 13)
    # The Friday after Thanksgiving, before the open: Wednesday.
    assert last_completed_session(et(2026, 11, 27, 9, 0)) == date(2026, 11, 25)


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        is_market_open(datetime(2026, 11, 18, 12, 0))
