"""Retry, circuit breaker, and rate-limit gate behaviour.

Nothing here sleeps. The breaker takes an injectable clock and the retry layer
an injectable sleep, so the reset window and the backoff are driven directly —
a suite that waits out a 60-second breaker window is a suite nobody runs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.core.exceptions import ExternalServiceError, NotFoundError
from app.core.rate_limit import TokenBucketLimiter
from app.integrations.market_data.base import Bar, Quote, SymbolInfo
from app.integrations.market_data.resilience import (
    BreakerState,
    CircuitBreaker,
    RateLimitGate,
    ResilientProvider,
    TransientProviderError,
    raise_for_provider_status,
)


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedProvider:
    """A provider that replays a scripted sequence of outcomes.

    Each entry is either an exception to raise or a value to return, so a test
    can say "fail twice, then succeed" without any HTTP at all.
    """

    name = "scripted"

    def __init__(self, outcomes: Sequence[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def _next(self) -> object:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def list_symbols(self) -> Sequence[SymbolInfo]:
        self._next()
        return [SymbolInfo(ticker="AAPL", name="Apple Inc.")]

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        self._next()
        return []

    async def get_quote(self, ticker: str) -> Quote:
        self._next()
        return Quote(ticker=ticker, price=Decimal("100.00"))

    async def get_quotes(self, tickers: Sequence[str]) -> Mapping[str, Quote]:
        self._next()
        return {}


async def _no_sleep(_: float) -> None:
    """Collapse backoff to nothing. The wait strategy is tenacity's problem."""


def build(
    outcomes: Sequence[object],
    *,
    clock: FakeClock | None = None,
    max_attempts: int = 3,
    failure_threshold: int = 5,
    reset_seconds: float = 60.0,
    capacity: int = 1_000,
) -> tuple[ResilientProvider, ScriptedProvider, CircuitBreaker]:
    inner = ScriptedProvider(outcomes)
    breaker = CircuitBreaker(
        name=inner.name,
        failure_threshold=failure_threshold,
        reset_seconds=reset_seconds,
        clock=clock or FakeClock(),
    )
    gate = RateLimitGate(
        limiter=TokenBucketLimiter(capacity=capacity, window_seconds=60.0),
        key=inner.name,
        sleep=_no_sleep,
    )
    wrapped = ResilientProvider(
        inner=inner,
        breaker=breaker,
        gate=gate,
        max_attempts=max_attempts,
        sleep=_no_sleep,
    )
    return wrapped, inner, breaker


# ------------------------------------------------------------------ status mapping


def _response(status: int) -> httpx.Response:
    return httpx.Response(status_code=status, request=httpx.Request("GET", "https://example.test"))


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_statuses_raise_a_transient_error(status: int) -> None:
    with pytest.raises(TransientProviderError):
        raise_for_provider_status(_response(status), provider="test")


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_client_errors_are_not_transient(status: int) -> None:
    """Never retry a 4xx other than 429 — it will not become a different answer."""
    with pytest.raises(ExternalServiceError) as caught:
        raise_for_provider_status(_response(status), provider="test")

    assert not isinstance(caught.value, TransientProviderError)


def test_a_404_for_a_ticker_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        raise_for_provider_status(_response(404), provider="test", ticker="NOPE")


def test_success_statuses_pass_through() -> None:
    raise_for_provider_status(_response(200), provider="test")


# ------------------------------------------------------------------------- retry


async def test_retries_a_transient_failure_and_then_succeeds() -> None:
    wrapped, inner, _ = build([TransientProviderError("upstream wobbled")])

    quote = await wrapped.get_quote("AAPL")

    assert quote.price == Decimal("100.00")
    assert inner.calls == 2


async def test_gives_up_after_the_configured_attempts() -> None:
    wrapped, inner, _ = build([TransientProviderError("down")] * 5, max_attempts=3)

    with pytest.raises(TransientProviderError):
        await wrapped.get_quote("AAPL")

    assert inner.calls == 3


async def test_retries_a_timeout() -> None:
    wrapped, inner, _ = build([httpx.ConnectTimeout("timed out")])

    await wrapped.get_quote("AAPL")

    assert inner.calls == 2


async def test_does_not_retry_a_non_transient_failure() -> None:
    wrapped, inner, _ = build([ExternalServiceError("malformed payload")])

    with pytest.raises(ExternalServiceError):
        await wrapped.get_quote("AAPL")

    assert inner.calls == 1, "a permanent failure must be tried exactly once"


async def test_does_not_retry_an_unknown_ticker() -> None:
    wrapped, inner, _ = build([NotFoundError("no such ticker")])

    with pytest.raises(NotFoundError):
        await wrapped.get_quote("NOPE")

    assert inner.calls == 1


# ----------------------------------------------------------------------- breaker


def test_breaker_opens_only_after_consecutive_failures() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(name="p", failure_threshold=3, reset_seconds=60.0, clock=clock)

    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED

    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow()


def test_a_success_resets_the_failure_run() -> None:
    breaker = CircuitBreaker(name="p", failure_threshold=3, reset_seconds=60.0, clock=FakeClock())

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state is BreakerState.CLOSED, "the counter is consecutive, not cumulative"


def test_breaker_allows_one_trial_after_the_reset_window() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(name="p", failure_threshold=1, reset_seconds=60.0, clock=clock)
    breaker.record_failure()

    assert not breaker.allow()

    clock.advance(60.0)
    assert breaker.allow(), "the window has elapsed, so one probe gets through"
    assert not breaker.allow(), "but only one — a burst must not all become probes"


def test_a_failed_trial_reopens_for_another_full_window() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(name="p", failure_threshold=1, reset_seconds=60.0, clock=clock)
    breaker.record_failure()
    clock.advance(60.0)
    assert breaker.allow()

    breaker.record_failure()

    assert breaker.state is BreakerState.OPEN
    clock.advance(59.0)
    assert not breaker.allow()
    clock.advance(1.0)
    assert breaker.allow()


def test_a_successful_trial_closes_the_breaker() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(name="p", failure_threshold=1, reset_seconds=60.0, clock=clock)
    breaker.record_failure()
    clock.advance(60.0)
    breaker.allow()

    breaker.record_success()

    assert breaker.state is BreakerState.CLOSED
    assert breaker.allow()


async def test_an_open_breaker_fails_fast_without_calling_the_provider() -> None:
    clock = FakeClock()
    wrapped, inner, _ = build(
        [TransientProviderError("down")] * 10,
        clock=clock,
        max_attempts=1,
        failure_threshold=2,
    )

    for _ in range(2):
        with pytest.raises(TransientProviderError):
            await wrapped.get_quote("AAPL")
    calls_before = inner.calls

    with pytest.raises(ExternalServiceError, match="circuit breaker open"):
        await wrapped.get_quote("AAPL")

    assert inner.calls == calls_before, "an open breaker must not reach the provider"


async def test_an_unknown_ticker_does_not_open_the_breaker() -> None:
    """One typo in a watchlist must not take the provider out for everyone."""
    wrapped, _, breaker = build([NotFoundError("nope")] * 10, failure_threshold=2, max_attempts=1)

    for _ in range(5):
        with pytest.raises(NotFoundError):
            await wrapped.get_quote("NOPE")

    assert breaker.state is BreakerState.CLOSED


async def test_recovery_after_the_window_reaches_the_provider_again() -> None:
    clock = FakeClock()
    wrapped, _inner, breaker = build(
        [TransientProviderError("down"), TransientProviderError("down")],
        clock=clock,
        max_attempts=1,
        failure_threshold=2,
        reset_seconds=30.0,
    )
    for _ in range(2):
        with pytest.raises(TransientProviderError):
            await wrapped.get_quote("AAPL")
    assert breaker.state is BreakerState.OPEN

    clock.advance(30.0)
    quote = await wrapped.get_quote("AAPL")

    assert quote.price == Decimal("100.00")
    assert breaker.state is BreakerState.CLOSED


# -------------------------------------------------------------------- rate limit


async def test_the_gate_waits_for_capacity_rather_than_rejecting() -> None:
    """Provider limits meter a quota; the right response is to wait, not to error."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)
        limiter.reset()  # stand in for time passing

    limiter = TokenBucketLimiter(capacity=2, window_seconds=60.0)
    gate = RateLimitGate(limiter=limiter, key="p", sleep=record)

    for _ in range(3):
        await gate.acquire()

    assert slept, "the third call exceeded the budget and should have waited"
    assert all(s > 0 for s in slept)


async def test_every_retry_attempt_spends_a_token() -> None:
    limiter = TokenBucketLimiter(capacity=10, window_seconds=60.0)
    inner = ScriptedProvider([TransientProviderError("wobble")])
    wrapped = ResilientProvider(
        inner=inner,
        breaker=CircuitBreaker(name="p", failure_threshold=5, reset_seconds=60.0),
        gate=RateLimitGate(limiter=limiter, key="p", sleep=_no_sleep),
        max_attempts=3,
        sleep=_no_sleep,
    )

    await wrapped.get_quote("AAPL")

    assert limiter.time_until_token("p") == 0.0
    remaining = [limiter.allow("p") for _ in range(10)]
    assert remaining.count(True) == 8, "two calls were made, so two tokens are gone"
