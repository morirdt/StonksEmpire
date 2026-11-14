"""The shared Redis client, and the guard every Redis caller goes through.

**Postgres is the source of truth and Redis never is.** Redis holds rate-limit
counts, circuit breaker state, the worker heartbeat, and pub/sub hints. If it is
flushed, nothing a user can see is lost. If it is unreachable, every caller
falls back to the in-process behaviour that predates it. So this module is built
around failing quickly and cheaply rather than around retrying.

- **No client-side retries.** redis-py retries with exponential backoff by
  default, which turns a refused connection into a stall of several seconds
  in front of every login during an outage. One attempt, a short timeout, then
  the fallback.
- **One guard, shared.** After a failure, :class:`RedisGuard` sends callers
  straight to their fallback for ``retry_after_seconds`` rather than letting
  each of them pay a connect timeout. It also logs the outage **once**, not once
  per call, and logs again when Redis comes back.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import structlog
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import get_settings

__all__ = [
    "REDIS_UNAVAILABLE",
    "RedisGuard",
    "close_redis",
    "get_redis",
    "redis_guard",
    "redis_is_healthy",
]

logger = structlog.stdlib.get_logger(__name__)

#: The failures that mean "Redis is not there", which send a caller to its
#: fallback. Deliberately not ``RedisError``: a ``ResponseError`` from a broken
#: Lua script is a bug, and falling back would hide it behind a warning.
REDIS_UNAVAILABLE: Final = (RedisConnectionError, RedisTimeoutError, OSError)

_client: Redis | None = None


def get_redis() -> Redis:
    """The process-wide client, built from settings on first use."""
    global _client
    if _client is None:
        settings = get_settings().redis
        _client = Redis.from_url(
            settings.url,
            decode_responses=True,
            socket_timeout=settings.timeout_seconds,
            socket_connect_timeout=settings.timeout_seconds,
            retry=Retry(NoBackoff(), retries=0),
        )
    return _client


async def close_redis() -> None:
    """Close and forget the client: at shutdown, and between tests.

    A redis.asyncio connection belongs to the event loop that opened it, and
    each test runs on a fresh loop, so a cached client must not outlive its
    test.
    """
    global _client
    client, _client = _client, None
    if client is not None:
        await client.aclose()


@dataclass
class RedisGuard:
    """Tracks whether Redis is reachable, so callers know when to skip it.

    ``available()`` is False for ``retry_after_seconds`` after a failure. The
    first call after that tries Redis again. If that call succeeds the outage is
    over, and if it fails the window restarts.
    """

    retry_after_seconds: float
    clock: Callable[[], float] = time.monotonic
    _down_since: float | None = field(default=None, init=False)
    _retry_at: float = field(default=0.0, init=False)

    @property
    def is_down(self) -> bool:
        return self._down_since is not None

    def available(self) -> bool:
        return self._down_since is None or self.clock() >= self._retry_at

    def failed(self, exc: BaseException, *, operation: str) -> None:
        now = self.clock()
        if self._down_since is None:
            self._down_since = now
            logger.warning(
                "redis_unavailable_using_fallback",
                operation=operation,
                error=f"{type(exc).__name__}: {exc}",
            )
        self._retry_at = now + self.retry_after_seconds

    def succeeded(self) -> None:
        if self._down_since is not None:
            logger.info("redis_recovered", outage_seconds=round(self.clock() - self._down_since, 1))
            self._down_since = None

    def reset(self) -> None:
        """Forget any outage. For tests."""
        self._down_since = None
        self._retry_at = 0.0


redis_guard = RedisGuard(retry_after_seconds=get_settings().redis.retry_after_seconds)


async def redis_is_healthy() -> bool:
    """For ``/ready``: does Redis answer a PING right now?

    This deliberately ignores the guard's back-off window. Readiness should
    report what is true now, not what was true when a caller last gave up.
    """
    try:
        return bool(await get_redis().ping())
    except REDIS_UNAVAILABLE:
        return False
