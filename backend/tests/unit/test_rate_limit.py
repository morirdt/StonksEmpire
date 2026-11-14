"""The in-process token bucket."""

from __future__ import annotations

from app.core.rate_limit import TokenBucketLimiter


def test_requests_within_the_allowance_pass() -> None:
    limiter = TokenBucketLimiter(capacity=3, window_seconds=60)

    assert [limiter.allow("1.2.3.4", now=0.0) for _ in range(3)] == [True] * 3


def test_the_next_request_is_refused() -> None:
    limiter = TokenBucketLimiter(capacity=3, window_seconds=60)
    for _ in range(3):
        limiter.allow("1.2.3.4", now=0.0)

    assert limiter.allow("1.2.3.4", now=0.0) is False


def test_keys_are_independent() -> None:
    """One noisy client must not lock everyone else out."""
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    limiter.allow("1.2.3.4", now=0.0)

    assert limiter.allow("5.6.7.8", now=0.0) is True


def test_the_bucket_refills_over_time() -> None:
    limiter = TokenBucketLimiter(capacity=2, window_seconds=60)
    limiter.allow("1.2.3.4", now=0.0)
    limiter.allow("1.2.3.4", now=0.0)
    assert limiter.allow("1.2.3.4", now=0.0) is False

    # Half the window restores half the capacity.
    assert limiter.allow("1.2.3.4", now=30.0) is True


def test_refill_is_smooth_rather_than_a_fixed_window() -> None:
    """A fixed window lets an attacker spend 2x capacity across the boundary."""
    limiter = TokenBucketLimiter(capacity=10, window_seconds=60)
    for _ in range(10):
        limiter.allow("1.2.3.4", now=0.0)

    # One second in, exactly one token has accrued — not the whole allowance.
    assert limiter.allow("1.2.3.4", now=6.0) is True
    assert limiter.allow("1.2.3.4", now=6.0) is False


def test_capacity_is_a_ceiling() -> None:
    """Idle time must not bank credit beyond the configured burst."""
    limiter = TokenBucketLimiter(capacity=2, window_seconds=60)

    assert limiter.allow("1.2.3.4", now=0.0) is True
    assert [limiter.allow("1.2.3.4", now=10_000.0) for _ in range(2)] == [True, True]
    assert limiter.allow("1.2.3.4", now=10_000.0) is False


def test_reset_clears_state() -> None:
    limiter = TokenBucketLimiter(capacity=1, window_seconds=60)
    limiter.allow("1.2.3.4", now=0.0)

    limiter.reset()

    assert limiter.allow("1.2.3.4", now=0.0) is True
