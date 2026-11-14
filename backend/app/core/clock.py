"""The one place that reads the wall clock.

Job logic never calls ``datetime.now()``. It takes a ``Clock`` and calls that,
so market hours, cooldowns, and "did this become true since last time" can be
tested at frozen instants rather than by waiting for them. Production code
passes :func:`utc_now`, and tests pass something they control.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.core.config import get_settings

__all__ = ["Clock", "utc_now"]

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current instant, aware, in UTC.

    Shifted by ``DEV_CLOCK_OFFSET`` when that is set, outside production only,
    so the market can be "open" for a demo on a weekend. Everything that asks
    *what time it is for the market* goes through here: the worker's jobs and
    ``Fake``'s quotes. Timestamps recording when a row was written do not, so
    they stay honest.
    """
    offset = get_settings().dev_clock_offset
    now = datetime.now(UTC)
    return now + offset if offset is not None else now
