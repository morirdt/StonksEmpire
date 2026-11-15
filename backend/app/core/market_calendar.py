"""When the US equity market is open: pure functions over an injected instant.

Every function takes the date or instant it is asked about. None of them reads
the clock, which is what makes market hours, holidays, and DST testable with
frozen instants rather than by waiting for them.

- **Times are computed in ``America/New_York`` and returned in UTC**, matching
  the project's timestamp convention. No UTC offset is hard-coded anywhere: the
  session opens at 13:30 UTC for half the year and at 14:30 for the other half,
  and ``zoneinfo`` is the only thing allowed to know which half it is.
- **Holidays come from the ``holidays`` package's NYSE calendar**, including
  observed dates and Good Friday. ``exchange_calendars`` would also model early
  closes, but it pulls in pandas, which this project keeps out on purpose.
- **Early closes (about three a year, at 13:00 ET) are treated as full
  sessions.** On those days the worker keeps polling for three hours after the
  close and gets the same price each time. Modelling half-days correctly would
  need a hand-kept table, which costs more than three hours of wasted polling.

The worker's jobs fire on plain schedules and ask this module whether to run.
Cron expressions cannot say "weekdays at 17:30 unless it is Good Friday", so
that rule lives here, in one tested place.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo

import holidays

__all__ = [
    "MARKET_TZ",
    "SESSION_CLOSE",
    "SESSION_OPEN",
    "is_market_open",
    "is_trading_day",
    "last_completed_session",
    "session_bounds",
]

MARKET_TZ: Final = ZoneInfo("America/New_York")
SESSION_OPEN: Final = time(9, 30)
SESSION_CLOSE: Final = time(16, 0)

#: How far back ``last_completed_session`` will look. The longest NYSE closure
#: in living memory is four trading days (after 9/11), so this is generous.
_MAX_LOOKBACK_DAYS: Final = 14


@lru_cache(maxsize=32)
def _nyse_holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.financial_holidays("NYSE", years=year))


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None:
        raise ValueError("A naive datetime has no meaning here; pass an aware one (UTC).")


def is_trading_day(day: date) -> bool:
    """A weekday that is not an NYSE holiday."""
    return day.weekday() < 5 and day not in _nyse_holidays(day.year)


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    """The regular session ``[open, close)`` for ``day``, in UTC, or ``None``."""
    if not is_trading_day(day):
        return None
    open_ = datetime.combine(day, SESSION_OPEN, tzinfo=MARKET_TZ)
    close = datetime.combine(day, SESSION_CLOSE, tzinfo=MARKET_TZ)
    return open_.astimezone(UTC), close.astimezone(UTC)


def is_market_open(at: datetime) -> bool:
    """Is ``at`` inside a regular session? Open at 09:30:00, closed at 16:00:00."""
    _require_aware(at)
    bounds = session_bounds(at.astimezone(MARKET_TZ).date())
    return bounds is not None and bounds[0] <= at < bounds[1]


def last_completed_session(at: datetime) -> date:
    """The most recent trading day whose close is at or before ``at``.

    At 15:59 ET on a trading day that is the previous trading day, and at 16:00
    it is today. This is the last date for which a daily bar is final, and so the
    last date a provider may serve one for.
    """
    _require_aware(at)
    day = at.astimezone(MARKET_TZ).date()
    for _ in range(_MAX_LOOKBACK_DAYS):
        bounds = session_bounds(day)
        if bounds is not None and bounds[1] <= at:
            return day
        day -= timedelta(days=1)
    raise RuntimeError(f"No completed session in the {_MAX_LOOKBACK_DAYS} days before {at}.")
