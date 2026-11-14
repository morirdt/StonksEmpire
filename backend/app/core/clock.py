"""The one place that reads the wall clock.

Job logic never calls ``datetime.now()``. It takes a ``Clock`` and calls that,
so market hours, cooldowns, and "did this become true since last time" can be
tested at frozen instants rather than by waiting for them. Production code
passes :func:`utc_now`, and tests pass something they control.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

__all__ = ["Clock", "utc_now"]

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The current instant, aware, in UTC."""
    return datetime.now(UTC)
