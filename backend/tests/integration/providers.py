"""Providers and publishers for tests that run the worker's jobs.

Shared by the job and alert suites. Not fixtures: each test builds what it
needs at its own frozen instant.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.core.exceptions import ExternalServiceError
from app.integrations.market_data.base import Bar, Quote, SymbolInfo
from app.integrations.market_data.fake import FakeMarketDataProvider
from tests.integration.conftest import SEED_TICKERS, FrozenClock

UNIVERSE = [SymbolInfo(ticker=t, name=n, exchange=e) for t, n, e, _ in SEED_TICKERS]


class RecordingProvider(FakeMarketDataProvider):
    """``Fake``, plus a record of every bar window it was asked for."""

    def __init__(self, clock: FrozenClock, symbols: Sequence[SymbolInfo] = UNIVERSE) -> None:
        super().__init__(symbols, clock=clock)
        self.bar_requests: list[tuple[str, date, date]] = []

    async def get_daily_bars(self, ticker: str, start: date, end: date) -> Sequence[Bar]:
        self.bar_requests.append((ticker, start, end))
        return await super().get_daily_bars(ticker, start, end)


class DownProvider(FakeMarketDataProvider):
    async def get_quotes(self, tickers: Sequence[str]) -> Mapping[str, Quote]:
        raise ExternalServiceError("fake is unavailable: circuit breaker open.")


class Published:
    """Collects push hints instead of sending them to Redis."""

    def __init__(self) -> None:
        self.events: list[Mapping[str, Any]] = []

    async def __call__(self, event: Mapping[str, Any]) -> None:
        self.events.append(event)
