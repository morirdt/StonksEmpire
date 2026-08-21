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
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Final

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.rate_limit import TokenBucketLimiter
from app.integrations.market_data.base import Bar, MarketDataProvider, Quote, SymbolInfo

__all__ = [
    "BreakerState",
    "CircuitBreaker",
    "RateLimitGate",
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
class CircuitBreaker:
    """Per-provider failure latch.

    Opens after ``failure_threshold`` *consecutive* failures, fails fast while
    open, and after ``reset_seconds`` lets exactly one trial call through. A
    trial that succeeds closes it; a trial that fails re-opens it for another
    full window.

    The clock is injectable so tests can drive the reset window directly instead
    of sleeping through it.
    """

    name: str
    failure_threshold: int
    reset_seconds: float
    clock: Callable[[], float] = time.monotonic

    _failures: int = 0
    _opened_at: float | None = None
    _trial_in_flight: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._trial_in_flight:
            return BreakerState.HALF_OPEN
        if self.clock() - self._opened_at >= self.reset_seconds:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    @property
    def is_healthy(self) -> bool:
        """Closed, or open long enough to be worth trying again."""
        return self.state is not BreakerState.OPEN

    def allow(self) -> bool:
        """May a call proceed? Claims the single trial slot when half-open."""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._trial_in_flight:
                # One trial at a time: a burst of callers must not all become
                # probes against a dependency that is probably still down.
                return False
            if self.clock() - self._opened_at >= self.reset_seconds:
                self._trial_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            if self._trial_in_flight:
                self._trial_in_flight = False
                self._opened_at = self.clock()
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self.clock()

    def reset(self) -> None:
        """Drop all state. For tests."""
        self.record_success()


@dataclass
class RateLimitGate:
    """Awaits capacity on a token bucket rather than rejecting."""

    limiter: TokenBucketLimiter
    key: str
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def acquire(self) -> None:
        while not self.limiter.allow(self.key):
            await self.sleep(self.limiter.time_until_token(self.key))


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
        if not self.breaker.allow():
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
            self.breaker.record_success()
            raise
        except Exception:
            self.breaker.record_failure()
            raise

        self.breaker.record_success()
        return result

    async def list_symbols(self) -> Sequence[SymbolInfo]:
        return await self._guard(lambda: self.inner.list_symbols())

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        return await self._guard(lambda: self.inner.get_daily_bars(ticker, start, end))

    async def get_quote(self, ticker: str) -> Quote:
        return await self._guard(lambda: self.inner.get_quote(ticker))

    async def get_quotes(self, tickers: Sequence[str]) -> Mapping[str, Quote]:
        return await self._guard(lambda: self.inner.get_quotes(tickers))
