"""The worker's heartbeat: how ``api`` (and Compose) know the worker is alive.

A worker that stops leaves alerts silently never firing, which looks exactly
like "the condition was never met". So the worker writes ``worker:heartbeat``
every 15 seconds with a 60-second TTL, and ``/ready`` and the container
healthcheck both read it. A missing key, or one older than 60 seconds, means
the worker is reported dead.

The heartbeat takes no advisory lock, unlike every other job. It writes nothing
to Postgres, so two workers both writing it is harmless. And it must not depend
on Postgres, so that a database outage does not also report the worker as dead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.redis import REDIS_UNAVAILABLE, get_redis

__all__ = ["HEARTBEAT_KEY", "MAX_HEARTBEAT_AGE", "beat", "worker_is_alive"]


def _wall_clock() -> datetime:
    """Real time, never ``DEV_CLOCK_OFFSET``-shifted.

    Liveness is about now, not about market time. A heartbeat on the shifted
    clock would read as days old to any process without the same offset.
    """
    return datetime.now(UTC)


HEARTBEAT_KEY: Final = "worker:heartbeat"
MAX_HEARTBEAT_AGE: Final = timedelta(seconds=60)


async def beat(clock: Clock = _wall_clock, redis: Redis | None = None) -> None:
    """Record that the worker is alive now."""
    client = redis or get_redis()
    await client.set(HEARTBEAT_KEY, clock().isoformat(), ex=int(MAX_HEARTBEAT_AGE.total_seconds()))


async def worker_is_alive(clock: Clock = _wall_clock, redis: Redis | None = None) -> bool:
    """Is there a heartbeat younger than ``MAX_HEARTBEAT_AGE``?

    The TTL already drops a dead worker's key. The age check also catches a
    worker whose clock has stopped advancing while the key is still alive.
    """
    client = redis or get_redis()
    try:
        raw = await client.get(HEARTBEAT_KEY)
    except REDIS_UNAVAILABLE:
        return False
    if raw is None:
        return False
    try:
        last = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    return clock() - last <= MAX_HEARTBEAT_AGE
