"""Symbol search, quotes, and the two chart reads.

Thin, per ``CLAUDE.md``: parse, call one service method, shape the response.
Every route requires an authenticated user — market data is not public here,
since serving it to anonymous callers would let anyone spend the provider quota.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core.enums import ChartRange
from app.schemas.market_data import (
    BarResponse,
    BarsResponse,
    IndicatorResponse,
    IndicatorsResponse,
    QuoteResponse,
    QuotesResponse,
    SymbolResponse,
    SymbolSearchResponse,
)
from app.services.chart_service import resolve_window
from app.services.market_data_service import MarketDataService

router = APIRouter(tags=["market-data"])

#: A ceiling on how many tickers one request may price, so a single call cannot
#: fan out into hundreds of provider requests.
MAX_QUOTE_TICKERS = 100


@router.get("/symbols", response_model=SymbolSearchResponse, summary="Search symbols")
async def search_symbols(
    session: DbSession,
    _user: CurrentUser,
    search: Annotated[str, Query(min_length=1, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SymbolSearchResponse:
    """Trigram search over ticker and name. Exact ticker matches rank first."""
    symbols = await MarketDataService(session).search_symbols(search, limit=limit)
    return SymbolSearchResponse(items=[SymbolResponse.model_validate(s) for s in symbols])


@router.get("/symbols/{ticker}", response_model=SymbolResponse, summary="Get one symbol")
async def get_symbol(
    session: DbSession,
    _user: CurrentUser,
    ticker: str,
) -> SymbolResponse:
    symbol = await MarketDataService(session).get_symbol(ticker)
    return SymbolResponse.model_validate(symbol)


@router.get("/quotes", response_model=QuotesResponse, summary="Get quotes by ticker")
async def get_quotes(
    session: DbSession,
    _user: CurrentUser,
    tickers: Annotated[str, Query(description="Comma-separated tickers, e.g. AAPL,MSFT")],
) -> QuotesResponse:
    """Cache-first. Anything past the TTL is refetched, the rest is served as stored.

    Unknown tickers are simply absent from the response rather than failing it.
    """
    wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()][:MAX_QUOTE_TICKERS]
    quotes = await MarketDataService(session).get_quotes(wanted)
    return QuotesResponse(
        quotes={ticker: QuoteResponse.from_row(ticker, quote) for ticker, quote in quotes.items()}
    )


#: The two chart reads share a query string, so they share its documentation.
_RANGE_QUERY = Query(
    alias="range",
    description="A preset window (1M, 3M, 6M, 1Y, 2Y, MAX). Mutually exclusive with start/end.",
)
_START_QUERY = Query(description="Explicit window start. Requires 'end' and excludes 'range'.")
_END_QUERY = Query(description="Explicit window end. Requires 'start' and excludes 'range'.")


@router.get(
    "/symbols/{ticker}/bars",
    response_model=BarsResponse,
    summary="Daily bars for one symbol",
)
async def get_bars(
    session: DbSession,
    _user: CurrentUser,
    ticker: str,
    chart_range: Annotated[ChartRange | None, _RANGE_QUERY] = None,
    start: Annotated[date | None, _START_QUERY] = None,
    end: Annotated[date | None, _END_QUERY] = None,
) -> BarsResponse:
    """OHLCV, oldest first. Unknown ticker is 404; range plus start/end is 422."""
    window = resolve_window(chart_range=chart_range, start=start, end=end)
    symbol, bars = await MarketDataService(session).get_bars(ticker, window)
    return BarsResponse(
        ticker=symbol.ticker,
        bars=[BarResponse.model_validate(bar) for bar in bars],
    )


@router.get(
    "/symbols/{ticker}/indicators",
    response_model=IndicatorsResponse,
    summary="Computed indicators for one symbol",
)
async def get_indicators(
    session: DbSession,
    _user: CurrentUser,
    ticker: str,
    chart_range: Annotated[ChartRange | None, _RANGE_QUERY] = None,
    start: Annotated[date | None, _START_QUERY] = None,
    end: Annotated[date | None, _END_QUERY] = None,
) -> IndicatorsResponse:
    """The same window as the bars endpoint, aligned by ``trade_date``.

    Nulls are real: a 200-day average has no value for its first 199 bars, and
    the chart must draw a gap there rather than a line to zero.
    """
    window = resolve_window(chart_range=chart_range, start=start, end=end)
    symbol, rows = await MarketDataService(session).get_indicators(ticker, window)
    return IndicatorsResponse(
        ticker=symbol.ticker,
        indicators=[IndicatorResponse.model_validate(row) for row in rows],
    )
