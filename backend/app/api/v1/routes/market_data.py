"""Symbol search and quotes.

Thin, per ``CLAUDE.md``: parse, call one service method, shape the response.
Both routes require an authenticated user — market data is not public here,
since serving it to anonymous callers would let anyone spend the provider quota.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.market_data import (
    QuoteResponse,
    QuotesResponse,
    SymbolResponse,
    SymbolSearchResponse,
)
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
