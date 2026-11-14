"""Token buckets: one in process memory, and one shared through Redis.

:class:`TokenBucketLimiter` is the in-process bucket from Phase 1. On its own it
is per-process, so with ``api`` and ``worker`` both running, each process would
hold a full allowance. That is harmless for login throttling but wrong for a
provider quota, which belongs to the account and not to a process.

:class:`SharedRateLimiter` keeps the same ``allow`` / ``time_until_token``
interface, made async, and keeps the bucket in Redis. **The read, refill, take,
and write happen in one Lua script**, so two processes can never both see "one
token left" and both take it. When Redis is unreachable it falls back to an
in-process :class:`TokenBucketLimiter`. Login limiting therefore degrades *to*
per-process limits rather than *open*: switching off credential-stuffing
protection whenever a cache is down would be disarming, not degrading.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol

from redis.asyncio import Redis

from app.core.redis import REDIS_UNAVAILABLE, RedisGuard, get_redis, redis_guard

__all__ = ["AsyncRateLimiter", "SharedRateLimiter", "TokenBucketLimiter"]


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class TokenBucketLimiter:
    """Allows ``capacity`` events per ``window_seconds``, refilling smoothly.

    A smooth refill rather than a fixed window avoids the burst that lets an
    attacker spend a full allowance on either side of a window boundary.
    """

    capacity: int
    window_seconds: float
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Consume one token for ``key``, returning False when exhausted."""
        moment = now if now is not None else time.monotonic()
        refill_rate = self.capacity / self.window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(tokens=self.capacity - 1, updated_at=moment)
                return True

            elapsed = max(0.0, moment - bucket.updated_at)
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * refill_rate)
            bucket.updated_at = moment

            if bucket.tokens < 1:
                return False
            bucket.tokens -= 1
            return True

    def time_until_token(self, key: str, *, now: float | None = None) -> float:
        """Seconds until ``key`` would be allowed again. ``0.0`` if it is now.

        Callers that must *wait* for capacity — the market data providers, which
        meter a quota rather than blunt an attack — use this to sleep exactly
        long enough instead of polling ``allow`` in a loop.
        """
        moment = now if now is not None else time.monotonic()
        refill_rate = self.capacity / self.window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0.0
            elapsed = max(0.0, moment - bucket.updated_at)
            tokens = min(self.capacity, bucket.tokens + elapsed * refill_rate)
            if tokens >= 1:
                return 0.0
            return (1 - tokens) / refill_rate

    def reset(self) -> None:
        """Drop all state. For tests, so one case cannot starve the next."""
        with self._lock:
            self._buckets.clear()


class AsyncRateLimiter(Protocol):
    """What a caller needs from a limiter, whichever process holds the state."""

    async def allow(self, key: str) -> bool: ...

    async def time_until_token(self, key: str) -> float: ...


#: KEYS[1] bucket. ARGV: capacity, refill per second, now (negative means
#: "use the Redis server's clock"), 1 to take a token or 0 to peek, TTL in ms.
#: Returns {allowed, seconds until a token, as a string}. A Lua number would be
#: truncated to an integer on the way out, which turns 0.4 s into 0.
#:
#: The server's clock is the default so that two processes, each with its own
#: idea of the time, still refill the same bucket consistently. Tests pass an
#: explicit ``now`` to step through time without sleeping.
_BUCKET_LUA: Final = """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
if now < 0 then
  local t = redis.call('TIME')
  now = tonumber(t[1]) + tonumber(t[2]) / 1000000
end
local take = ARGV[4] == '1'

local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)

local allowed = 0
if take then
  if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
  end
  redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
  redis.call('PEXPIRE', KEYS[1], ARGV[5])
end

local wait = 0
if tokens < 1 then
  wait = (1 - tokens) / rate
end
return {allowed, tostring(wait)}
"""


@dataclass
class SharedRateLimiter:
    """A token bucket per key, held in Redis under ``{prefix}:{key}``.

    Same smooth refill as :class:`TokenBucketLimiter`. A bucket key expires once
    it would have refilled completely anyway, so idle IPs do not accumulate.

    ``redis`` is a factory, not a client. ``None`` means in-process only, which
    is what the unit tests and the CLI scripts want.
    """

    capacity: int
    window_seconds: float
    prefix: str
    redis: Callable[[], Redis] | None = get_redis
    guard: RedisGuard = field(default_factory=lambda: redis_guard)
    now: Callable[[], float] | None = None
    _fallback: TokenBucketLimiter = field(init=False)

    def __post_init__(self) -> None:
        self._fallback = TokenBucketLimiter(
            capacity=self.capacity, window_seconds=self.window_seconds
        )

    def _key(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    async def _run(self, key: str, *, take: bool) -> tuple[bool, float] | None:
        """Run the script, or return ``None`` when the caller should fall back."""
        if self.redis is None or not self.guard.available():
            return None
        try:
            allowed, wait = await self.redis().eval(
                _BUCKET_LUA,
                1,
                self._key(key),
                self.capacity,
                self.capacity / self.window_seconds,
                self.now() if self.now is not None else -1,
                1 if take else 0,
                math.ceil(self.window_seconds * 1000),
            )
        except REDIS_UNAVAILABLE as exc:
            self.guard.failed(exc, operation=f"rate_limit:{self.prefix}")
            return None
        self.guard.succeeded()
        return bool(int(allowed)), float(wait)

    async def allow(self, key: str) -> bool:
        """Consume one token for ``key``, returning False when exhausted."""
        result = await self._run(key, take=True)
        if result is None:
            return self._fallback.allow(self._key(key), now=self.now() if self.now else None)
        return result[0]

    async def time_until_token(self, key: str) -> float:
        """Seconds until ``key`` would be allowed again. ``0.0`` if it is now."""
        result = await self._run(key, take=False)
        if result is None:
            return self._fallback.time_until_token(
                self._key(key), now=self.now() if self.now else None
            )
        return result[1]

    async def reset(self) -> None:
        """Drop all state under this prefix, shared and local. For tests."""
        self._fallback.reset()
        if self.redis is None:
            return
        client = self.redis()
        try:
            keys = [k async for k in client.scan_iter(match=f"{self.prefix}:*")]
            if keys:
                await client.delete(*keys)
        except REDIS_UNAVAILABLE:
            pass
