"""Rate limiting, retry, and circuit breaking for provider calls.

Three separate concerns, deliberately kept separate and composed in one place
rather than sprinkled through each implementation. A provider's job is to talk
to its API; this module's job is to make that safe to do repeatedly against a
third party that will, eventually, misbehave.

Applied in this order for a single call:

1. **Breaker** — if it is open, fail immediately without spending a rate-limit
   token or a retry. There is no point queueing behind a dependency we already
   know is down.
2. **Rate limit** — a token bucket per provider, awaited rather than rejected.
   These limits meter a quota; the right response to hitting one is to wait, not
   to error. Every retry attempt passes through it too.
3. **Retry** — exponential backoff with jitter, on transient failures only.

The breaker is hand-rolled rather than pulled from a library because it needs
per-provider state that ``/ready`` can inspect, which the decorator shape the
libraries offer does not give you. ``tenacity`` *is* used, because backoff
jitter is easy to get subtly wrong and there is nothing to inspect.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Final, Protocol

import httpx
from redis.asyncio import Redis
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.rate_limit import AsyncRateLimiter
from app.core.redis import REDIS_UNAVAILABLE, RedisGuard, get_redis, redis_guard
from app.integrations.market_data.base import Bar, MarketDataProvider, Quote, SymbolInfo

__all__ = [
    "BreakerState",
    "BreakerStore",
    "CircuitBreaker",
    "FallbackBreakerStore",
    "InMemoryBreakerStore",
    "RateLimitGate",
    "RedisBreakerStore",
    "ResilientProvider",
    "TransientProviderError",
    "raise_for_provider_status",
]


class TransientProviderError(ExternalServiceError):
    """An upstream failure that is worth trying again.

    The distinction matters twice over: only these are retried, and only these
    (plus the transport errors below) count toward opening the breaker. A bad
    ticker is neither.
    """


#: Transport-level failures are always transient — nothing reached the upstream.
#: ``TransportError`` is the base of the timeout, connection, and proxy errors,
#: so naming it covers all of them.
_RETRYABLE_TRANSPORT: Final = httpx.TransportError


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, TransientProviderError | _RETRYABLE_TRANSPORT)


def raise_for_provider_status(
    response: httpx.Response,
    *,
    provider: str,
    ticker: str | None = None,
) -> None:
    """Translate an HTTP status into this project's error vocabulary.

    The one rule worth stating out loud: **never retry a 4xx other than 429.**
    A malformed request or an unknown ticker is not going to start working, and
    retrying it burns quota to reach the same answer more slowly.
    """
    status = response.status_code
    if status < 400:
        return

    if status == 404 and ticker is not None:
        raise NotFoundError(f"Unknown ticker {ticker!r}.")
    if status == 429 or status >= 500:
        raise TransientProviderError(
            f"{provider} returned {status}.",
            extra={"provider": provider, "status": status},
        )
    raise ExternalServiceError(
        f"{provider} rejected the request with {status}.",
        extra={"provider": provider, "status": status},
    )


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _LocalBreakerState:
    failures: int = 0
    opened_at: float | None = None
    trial_started_at: float | None = None


class BreakerStore(Protocol):
    """Where a breaker's state lives. One state machine, two homes.

    Every method takes ``now``. ``None`` means "use your own clock": monotonic
    time in process, or the Redis server's clock when the state is shared, so
    two processes with different clocks still agree on how long it has been
    open.
    """

    async def state(self, name: str, reset_seconds: float, now: float | None) -> BreakerState: ...

    async def allow(self, name: str, reset_seconds: float, now: float | None) -> bool: ...

    async def record_success(self, name: str) -> None: ...

    async def record_failure(self, name: str, threshold: int, now: float | None) -> None: ...

    async def reset(self, name: str) -> None: ...


@dataclass
class InMemoryBreakerStore:
    """Breaker state in process memory: the Phase 2 behaviour, and the fallback."""

    _states: dict[str, _LocalBreakerState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _get(self, name: str) -> _LocalBreakerState:
        return self._states.setdefault(name, _LocalBreakerState())

    @staticmethod
    def _now(now: float | None) -> float:
        return time.monotonic() if now is None else now

    async def state(self, name: str, reset_seconds: float, now: float | None) -> BreakerState:
        with self._lock:
            s = self._get(name)
            moment = self._now(now)
            if s.opened_at is None:
                return BreakerState.CLOSED
            if s.trial_started_at is not None and moment - s.trial_started_at < reset_seconds:
                return BreakerState.HALF_OPEN
            if moment - s.opened_at >= reset_seconds:
                return BreakerState.HALF_OPEN
            return BreakerState.OPEN

    async def allow(self, name: str, reset_seconds: float, now: float | None) -> bool:
        with self._lock:
            s = self._get(name)
            moment = self._now(now)
            if s.opened_at is None:
                return True
            if s.trial_started_at is not None and moment - s.trial_started_at < reset_seconds:
                # One trial at a time: a burst of callers must not all become
                # probes against a dependency that is probably still down.
                return False
            if moment - s.opened_at >= reset_seconds:
                s.trial_started_at = moment
                return True
            return False

    async def record_success(self, name: str) -> None:
        with self._lock:
            self._states.pop(name, None)

    async def record_failure(self, name: str, threshold: int, now: float | None) -> None:
        with self._lock:
            s = self._get(name)
            moment = self._now(now)
            if s.trial_started_at is not None:
                s.trial_started_at = None
                s.opened_at = moment
                return
            s.failures += 1
            if s.failures >= threshold:
                s.opened_at = moment

    async def reset(self, name: str) -> None:
        await self.record_success(name)


#: Shared by every script below: resolve ``now`` (ARGV[1]) and load the hash.
#: A negative ``now`` means "use the server's clock". Times are stored as
#: strings because a Lua number round-trips through Redis as an integer.
_BREAKER_PRELUDE: Final = """
local now = tonumber(ARGV[1])
if now < 0 then
  local t = redis.call('TIME')
  now = tonumber(t[1]) + tonumber(t[2]) / 1000000
end
local h = redis.call('HMGET', KEYS[1], 'failures', 'opened_at', 'trial_started_at')
local failures = tonumber(h[1]) or 0
local opened_at = tonumber(h[2])
local trial_at = tonumber(h[3])
"""

#: ARGV: now, reset_seconds. Returns 'closed' | 'open' | 'half_open'.
_BREAKER_STATE_LUA: Final = (
    _BREAKER_PRELUDE
    + """
local reset = tonumber(ARGV[2])
if opened_at == nil then return 'closed' end
if trial_at ~= nil and now - trial_at < reset then return 'half_open' end
if now - opened_at >= reset then return 'half_open' end
return 'open'
"""
)

#: ARGV: now, reset_seconds. Returns 1 if the call may proceed. Claims the trial
#: when half-open. A claimed trial goes stale after ``reset_seconds``, which the
#: in-process breaker never needed: a process that dies mid-trial loses its
#: in-memory state with it, but its claim in Redis would otherwise block
#: every other process from probing again.
_BREAKER_ALLOW_LUA: Final = (
    _BREAKER_PRELUDE
    + """
local reset = tonumber(ARGV[2])
if opened_at == nil then return 1 end
if trial_at ~= nil and now - trial_at < reset then return 0 end
if now - opened_at >= reset then
  redis.call('HSET', KEYS[1], 'trial_started_at', tostring(now))
  return 1
end
return 0
"""
)

#: ARGV: now, threshold.
_BREAKER_FAILURE_LUA: Final = (
    _BREAKER_PRELUDE
    + """
if trial_at ~= nil then
  redis.call('HDEL', KEYS[1], 'trial_started_at')
  redis.call('HSET', KEYS[1], 'opened_at', tostring(now))
  return 0
end
failures = redis.call('HINCRBY', KEYS[1], 'failures', 1)
if failures >= tonumber(ARGV[2]) then
  redis.call('HSET', KEYS[1], 'opened_at', tostring(now))
end
return 0
"""
)


@dataclass
class RedisBreakerStore:
    """Breaker state in Redis under ``breaker:{name}``, shared by every process.

    Shared for a reason beyond correctness: ``/ready`` runs in ``api``, but the
    worker makes almost all the provider calls. A per-process breaker would
    report the API's view of a provider the API hardly touches.

    Raises the ``REDIS_UNAVAILABLE`` errors rather than handling them.
    :class:`FallbackBreakerStore` decides what an outage means.
    """

    redis: Callable[[], Redis] = get_redis

    @staticmethod
    def _key(name: str) -> str:
        return f"breaker:{name}"

    @staticmethod
    def _now(now: float | None) -> float:
        return -1.0 if now is None else now

    async def _eval(self, script: str, name: str, *args: float | int) -> object:
        return await self.redis().eval(script, 1, self._key(name), *args)

    async def state(self, name: str, reset_seconds: float, now: float | None) -> BreakerState:
        raw = await self._eval(_BREAKER_STATE_LUA, name, self._now(now), reset_seconds)
        return BreakerState(str(raw))

    async def allow(self, name: str, reset_seconds: float, now: float | None) -> bool:
        raw = await self._eval(_BREAKER_ALLOW_LUA, name, self._now(now), reset_seconds)
        return int(str(raw)) == 1

    async def record_success(self, name: str) -> None:
        await self.redis().delete(self._key(name))

    async def record_failure(self, name: str, threshold: int, now: float | None) -> None:
        await self._eval(_BREAKER_FAILURE_LUA, name, self._now(now), threshold)

    async def reset(self, name: str) -> None:
        await self.record_success(name)


@dataclass
class FallbackBreakerStore:
    """Redis while it answers, process memory while it does not.

    During an outage each process trips on its own failures, which is exactly
    the Phase 2 behaviour. When Redis comes back the shared state picks up
    where it was. That is acceptable for a breaker: the worst case is a few
    extra calls before it re-opens.
    """

    primary: RedisBreakerStore = field(default_factory=RedisBreakerStore)
    fallback: InMemoryBreakerStore = field(default_factory=InMemoryBreakerStore)
    guard: RedisGuard = field(default_factory=lambda: redis_guard)

    async def _call[T](
        self,
        operation: str,
        shared: Callable[[], Awaitable[T]],
        local: Callable[[], Awaitable[T]],
    ) -> T:
        if self.guard.available():
            try:
                result = await shared()
            except REDIS_UNAVAILABLE as exc:
                self.guard.failed(exc, operation=f"breaker:{operation}")
            else:
                self.guard.succeeded()
                return result
        return await local()

    async def state(self, name: str, reset_seconds: float, now: float | None) -> BreakerState:
        return await self._call(
            "state",
            lambda: self.primary.state(name, reset_seconds, now),
            lambda: self.fallback.state(name, reset_seconds, now),
        )

    async def allow(self, name: str, reset_seconds: float, now: float | None) -> bool:
        return await self._call(
            "allow",
            lambda: self.primary.allow(name, reset_seconds, now),
            lambda: self.fallback.allow(name, reset_seconds, now),
        )

    async def record_success(self, name: str) -> None:
        await self._call(
            "record_success",
            lambda: self.primary.record_success(name),
            lambda: self.fallback.record_success(name),
        )

    async def record_failure(self, name: str, threshold: int, now: float | None) -> None:
        await self._call(
            "record_failure",
            lambda: self.primary.record_failure(name, threshold, now),
            lambda: self.fallback.record_failure(name, threshold, now),
        )

    async def reset(self, name: str) -> None:
        await self.fallback.reset(name)
        with contextlib.suppress(*REDIS_UNAVAILABLE):
            await self.primary.reset(name)


@dataclass
class CircuitBreaker:
    """Per-provider failure latch.

    Opens after ``failure_threshold`` *consecutive* failures, fails fast while
    open, and after ``reset_seconds`` lets exactly one trial call through. A
    trial that succeeds closes it; a trial that fails re-opens it for another
    full window.

    The state machine is the same wherever the state lives. ``store`` decides
    that: in process by default, and shared through Redis when the app builds it
    (see ``app.integrations.market_data``).

    ``clock`` is injectable so tests can drive the reset window directly instead
    of sleeping through it. Left as ``None``, each store uses its own clock.
    """

    name: str
    failure_threshold: int
    reset_seconds: float
    clock: Callable[[], float] | None = None
    store: BreakerStore = field(default_factory=InMemoryBreakerStore)

    def _now(self) -> float | None:
        return self.clock() if self.clock is not None else None

    async def state(self) -> BreakerState:
        return await self.store.state(self.name, self.reset_seconds, self._now())

    async def is_healthy(self) -> bool:
        """Closed, or open long enough to be worth trying again."""
        return await self.state() is not BreakerState.OPEN

    async def allow(self) -> bool:
        """May a call proceed? Claims the single trial slot when half-open."""
        return await self.store.allow(self.name, self.reset_seconds, self._now())

    async def record_success(self) -> None:
        await self.store.record_success(self.name)

    async def record_failure(self) -> None:
        await self.store.record_failure(self.name, self.failure_threshold, self._now())

    async def reset(self) -> None:
        """Drop all state. For tests."""
        await self.store.reset(self.name)


@dataclass
class RateLimitGate:
    """Awaits capacity on a token bucket rather than rejecting."""

    limiter: AsyncRateLimiter
    key: str
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def acquire(self) -> None:
        while not await self.limiter.allow(self.key):
            await self.sleep(await self.limiter.time_until_token(self.key))


@dataclass
class ResilientProvider:
    """Wraps any provider so callers get the same guarantees from all of them.

    Satisfies ``MarketDataProvider`` itself, so it is a drop-in: nothing
    downstream knows whether it holds a raw implementation or a wrapped one.
    Keeping resilience here rather than in each provider is what lets ``Fake``
    stay a few dozen lines of arithmetic, and what makes the retry and breaker
    behaviour testable without any provider at all.
    """

    inner: MarketDataProvider
    breaker: CircuitBreaker
    gate: RateLimitGate
    max_attempts: int = 3
    sleep: Callable[[float], Awaitable[None]] | None = None

    @property
    def name(self) -> str:
        return self.inner.name

    async def _guard[T](self, call: Callable[[], Awaitable[T]]) -> T:
        if not await self.breaker.allow():
            raise ExternalServiceError(
                f"{self.name} is unavailable: circuit breaker open.",
                extra={"provider": self.name, "breaker": BreakerState.OPEN.value},
            )

        async def attempt() -> T:
            await self.gate.acquire()
            return await call()

        retrying = AsyncRetrying(
            sleep=self.sleep if self.sleep is not None else asyncio.sleep,
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(initial=0.2, max=5.0),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )

        try:
            result: T = await retrying(attempt)
        except NotFoundError:
            # An unknown ticker is a caller mistake, not an outage. Counting it
            # would let one typo in a watchlist trip the breaker for everyone.
            await self.breaker.record_success()
            raise
        except Exception:
            await self.breaker.record_failure()
            raise

        await self.breaker.record_success()
        return result

    async def list_symbols(self) -> Sequence[SymbolInfo]:
        return await self._guard(lambda: self.inner.list_symbols())

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        return await self._guard(lambda: self.inner.get_daily_bars(ticker, start, end))

    async def get_quote(self, ticker: str) -> Quote:
        return await self._guard(lambda: self.inner.get_quote(ticker))

    async def get_quotes(self, tickers: Sequence[str]) -> Mapping[str, Quote]:
        return await self._guard(lambda: self.inner.get_quotes(tickers))
