"""State shared through Redis: the rate limiter and the circuit breaker.

Two processes (``api`` and ``worker``) now spend one provider account and must
agree on one breaker. "Two processes" is modelled here as two independently
built limiter or breaker objects pointing at the same Redis, which is what the
second process holds.

The breaker scenarios are imported from the unit suite and re-run here with the
``breaker_store`` fixture overridden. A store that diverges from the in-process
state machine fails the same named test in both places.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import structlog
from httpx import AsyncClient
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

import app.api.health as health_module
import app.core.redis as redis_module
from app.core.config import get_settings
from app.core.rate_limit import SharedRateLimiter
from app.core.redis import RedisGuard, get_redis
from app.integrations.market_data.resilience import (
    BreakerState,
    BreakerStore,
    CircuitBreaker,
    FallbackBreakerStore,
    InMemoryBreakerStore,
    RedisBreakerStore,
)
from tests.unit.test_market_data_resilience import (  # noqa: F401 - re-collected here
    FakeClock,
    test_a_failed_trial_reopens_for_another_full_window,
    test_a_success_resets_the_failure_run,
    test_a_successful_trial_closes_the_breaker,
    test_an_abandoned_trial_goes_stale_after_a_window,
    test_breaker_allows_one_trial_after_the_reset_window,
    test_breaker_opens_only_after_consecutive_failures,
    test_breakers_are_independent_per_provider,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def breaker_store() -> BreakerStore:
    """Overrides the unit suite's in-memory store for the imported scenarios."""
    return RedisBreakerStore()


def _unreachable() -> Redis:
    """A client for a port nothing listens on: connection refused, immediately."""
    return Redis.from_url(
        "redis://127.0.0.1:1/0",
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=Retry(NoBackoff(), retries=0),
    )


@pytest.fixture
async def unreachable() -> AsyncGenerator[Redis]:
    client = _unreachable()
    yield client
    await client.aclose()


@pytest.fixture
def redis_down(unreachable: Redis, monkeypatch: pytest.MonkeyPatch) -> Redis:
    """Point the process-wide client at nothing, as if Redis had stopped."""
    monkeypatch.setattr(redis_module, "_client", unreachable)
    return unreachable


# ------------------------------------------------------------------ rate limit


def _limiter(capacity: int, *, prefix: str = "ratelimit:test", **kw: object) -> SharedRateLimiter:
    return SharedRateLimiter(
        capacity=capacity,
        window_seconds=60.0,
        prefix=prefix,
        guard=RedisGuard(retry_after_seconds=5.0),
        **kw,  # type: ignore[arg-type]
    )


async def test_the_bucket_is_shared_between_processes() -> None:
    """The whole point: api and worker draw from one provider quota."""
    clock = FakeClock()
    api = _limiter(3, now=clock)
    worker = _limiter(3, now=clock)

    assert await api.allow("p")
    assert await worker.allow("p")
    assert await api.allow("p")

    assert not await worker.allow("p"), "three tokens were spent across both processes"
    assert await api.time_until_token("p") == pytest.approx(20.0)


async def test_the_shared_bucket_refills_smoothly() -> None:
    clock = FakeClock()
    limiter = _limiter(2, now=clock)
    assert await limiter.allow("p")
    assert await limiter.allow("p")
    assert not await limiter.allow("p")

    clock.advance(30.0)

    assert await limiter.allow("p"), "half the window refills one of two tokens"
    assert not await limiter.allow("p")


async def test_keys_do_not_share_a_bucket() -> None:
    limiter = _limiter(1)

    assert await limiter.allow("1.2.3.4")
    assert await limiter.allow("5.6.7.8")


async def test_taking_the_last_token_is_atomic() -> None:
    """Two processes racing for one remaining token: exactly one wins.

    A read-then-write from Python would let both read "one left". The Lua
    script reads, refills, takes, and writes in one step.
    """
    seed = _limiter(5)
    for _ in range(4):
        assert await seed.allow("race")

    contenders = [_limiter(5) for _ in range(20)]
    results = await asyncio.gather(*(c.allow("race") for c in contenders))

    assert results.count(True) == 1


async def test_the_bucket_expires_once_it_would_be_full_anyway() -> None:
    limiter = _limiter(10, prefix="ratelimit:ttl")
    await limiter.allow("p")

    ttl_ms = await get_redis().pttl("ratelimit:ttl:p")

    assert 0 < ttl_ms <= 60_000


async def test_with_redis_unreachable_the_limiter_falls_back_rather_than_opening(
    unreachable: Redis,
) -> None:
    limiter = _limiter(2, redis=lambda: unreachable)

    assert await limiter.allow("p")
    assert await limiter.allow("p")

    assert not await limiter.allow("p"), "an outage must not switch the limit off"


async def test_an_outage_is_logged_once_not_per_call(unreachable: Redis) -> None:
    clock = FakeClock()
    guard = RedisGuard(retry_after_seconds=5.0, clock=clock)
    limiter = SharedRateLimiter(
        capacity=100, window_seconds=60.0, prefix="p", redis=lambda: unreachable, guard=guard
    )

    with structlog.testing.capture_logs() as logs:
        for _ in range(5):
            await limiter.allow("k")
            clock.advance(10.0)  # past the back-off, so every call really tries

    outages = [e for e in logs if e["event"] == "redis_unavailable_using_fallback"]
    assert len(outages) == 1


async def test_the_guard_skips_redis_inside_the_back_off_window(unreachable: Redis) -> None:
    """An unreachable host must not cost every request a connect timeout."""
    clock = FakeClock()
    guard = RedisGuard(retry_after_seconds=5.0, clock=clock)
    attempts = 0

    def counting() -> Redis:
        nonlocal attempts
        attempts += 1
        return unreachable

    limiter = SharedRateLimiter(
        capacity=100, window_seconds=60.0, prefix="p", redis=counting, guard=guard
    )

    for _ in range(10):
        await limiter.allow("k")
    assert attempts == 1

    clock.advance(5.0)
    await limiter.allow("k")
    assert attempts == 2


async def test_login_limiting_falls_back_when_redis_is_down(
    client: AsyncClient,
    redis_down: Redis,
) -> None:
    """Credential-stuffing protection degrades to per-process; it never opens."""
    limit = get_settings().auth.login_attempts_per_minute
    payload = {"email": "nobody@example.com", "password": "a-wrong-password-here"}

    for _ in range(limit):
        assert (await client.post("/api/v1/auth/login", json=payload)).status_code == 401

    throttled = await client.post("/api/v1/auth/login", json=payload)

    assert throttled.status_code == 429


# --------------------------------------------------------------------- breaker


async def test_the_breaker_is_shared_between_processes() -> None:
    """/ready runs in api, but the worker makes the calls that trip it."""
    clock = FakeClock()
    worker = CircuitBreaker(
        name="p", failure_threshold=2, reset_seconds=60.0, clock=clock, store=RedisBreakerStore()
    )
    api = CircuitBreaker(
        name="p", failure_threshold=2, reset_seconds=60.0, clock=clock, store=RedisBreakerStore()
    )

    await worker.record_failure()
    await worker.record_failure()

    assert await api.state() is BreakerState.OPEN
    assert not await api.allow()


async def test_the_breaker_uses_the_server_clock_by_default() -> None:
    """With no injected clock, opened_at comes from Redis, not from either process."""
    breaker = CircuitBreaker(
        name="p", failure_threshold=1, reset_seconds=60.0, store=RedisBreakerStore()
    )

    await breaker.record_failure()

    assert await breaker.state() is BreakerState.OPEN


async def test_the_breaker_falls_back_to_process_memory_when_redis_is_down(
    unreachable: Redis,
) -> None:
    store = FallbackBreakerStore(
        primary=RedisBreakerStore(redis=lambda: unreachable),
        fallback=InMemoryBreakerStore(),
        guard=RedisGuard(retry_after_seconds=5.0),
    )
    breaker = CircuitBreaker(name="p", failure_threshold=2, reset_seconds=60.0, store=store)

    await breaker.record_failure()
    await breaker.record_failure()

    assert await breaker.state() is BreakerState.OPEN, "it still trips on its own failures"


# ----------------------------------------------------------------------- ready


async def test_ready_reports_redis_healthy(client: AsyncClient) -> None:
    body = (await client.get("/ready")).json()

    assert body["checks"]["redis"] is True


async def test_ready_with_redis_stopped_is_degraded_not_failed(
    client: AsyncClient,
    redis_down: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ok() -> bool:
        return True

    # The rolled-back test session is not the app's engine; the database check
    # is covered elsewhere and is not what this test is about.
    monkeypatch.setattr(health_module, "check_database", ok)

    response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["redis"] is False
    assert "redis" in body["degraded"]
