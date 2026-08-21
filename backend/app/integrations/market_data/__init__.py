"""Market data integration: provider selection and shared breaker state.

One place decides which provider the app talks to and wraps it in the
resilience layer, so no caller ever constructs a provider itself. The circuit
breakers live here too, because ``/ready`` needs to report on them and the
service layer needs to trip them — and neither should own the other.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import MarketDataSettings, get_settings
from app.core.rate_limit import TokenBucketLimiter
from app.integrations.market_data.base import Bar, MarketDataProvider, Quote, SymbolInfo
from app.integrations.market_data.fake import FakeMarketDataProvider
from app.integrations.market_data.resilience import (
    BreakerState,
    CircuitBreaker,
    RateLimitGate,
    ResilientProvider,
)

__all__ = [
    "Bar",
    "MarketDataProvider",
    "Quote",
    "SymbolInfo",
    "breaker_states",
    "get_market_data_provider",
    "market_data_is_healthy",
    "reset_market_data_provider",
]

#: Breakers by provider name. Populated as providers are built, read by /ready.
_BREAKERS: dict[str, CircuitBreaker] = {}


def _build_provider(settings: MarketDataSettings) -> MarketDataProvider:
    match settings.provider:
        case "fake":
            return FakeMarketDataProvider()
        case "finnhub" | "tiingo":
            # Specified in docs/phases/phase-2-market-data.md but not built:
            # no account exists yet. The protocol and everything downstream of
            # it are ready for one — see that spec's "Decisions" section.
            raise NotImplementedError(
                f"The {settings.provider!r} provider is specified but not implemented. "
                "Set MARKET_DATA__PROVIDER=fake."
            )
        case _:  # pragma: no cover - Literal type makes this unreachable
            raise ValueError(f"Unknown market data provider {settings.provider!r}.")


@lru_cache(maxsize=1)
def get_market_data_provider() -> MarketDataProvider:
    """The configured provider, wrapped in rate limiting, retry, and a breaker.

    Cached: the breaker and the token bucket are stateful, and building a fresh
    one per request would mean a breaker that never opens and a rate limit that
    never bites.
    """
    settings = get_settings().market_data
    inner = _build_provider(settings)

    breaker = CircuitBreaker(
        name=inner.name,
        failure_threshold=settings.breaker_failure_threshold,
        reset_seconds=settings.breaker_reset_seconds,
    )
    _BREAKERS[inner.name] = breaker

    return ResilientProvider(
        inner=inner,
        breaker=breaker,
        gate=RateLimitGate(
            limiter=TokenBucketLimiter(
                capacity=settings.requests_per_minute,
                window_seconds=60.0,
            ),
            key=inner.name,
        ),
        max_attempts=settings.max_retries,
    )


def breaker_states() -> dict[str, BreakerState]:
    """Current breaker state per provider, for readiness reporting."""
    return {name: breaker.state for name, breaker in _BREAKERS.items()}


def market_data_is_healthy() -> bool:
    """True when no provider's breaker is fully open.

    A provider being down is a *degraded* dependency, not a fatal one: the app
    still serves cached quotes, watchlists, and everything that does not touch
    the upstream. ``/ready`` reports this by name but must not go red on it,
    or one third-party hiccup takes the whole deployment out of rotation.
    """
    return all(state is not BreakerState.OPEN for state in breaker_states().values())


def reset_market_data_provider() -> None:
    """Drop the cached provider and its breaker state. For tests."""
    get_market_data_provider.cache_clear()
    _BREAKERS.clear()
