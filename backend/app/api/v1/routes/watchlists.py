"""Watchlist CRUD and the quote grid.

Every route is scoped to the calling user by the service, and a watchlist
belonging to someone else comes back **404, not 403** — a 403 would confirm the
row exists. ``tests/integration/test_cross_user_authorization.py`` enforces this
for every path listed here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession
from app.models.watchlist import Watchlist
from app.schemas.market_data import QuoteResponse, SymbolResponse
from app.schemas.watchlist import (
    WatchlistCreateRequest,
    WatchlistDetailResponse,
    WatchlistItemCreateRequest,
    WatchlistItemResponse,
    WatchlistReorderRequest,
    WatchlistResponse,
    WatchlistRowResponse,
    WatchlistUpdateRequest,
)
from app.services.market_data_service import MarketDataService
from app.services.watchlist_service import WatchlistService

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


def _to_response(watchlist: Watchlist) -> WatchlistResponse:
    return WatchlistResponse(
        id=watchlist.id,
        name=watchlist.name,
        is_default=watchlist.is_default,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        item_count=len(watchlist.items),
    )


@router.get("", response_model=list[WatchlistResponse], summary="List your watchlists")
async def list_watchlists(session: DbSession, user: CurrentUser) -> list[WatchlistResponse]:
    watchlists = await WatchlistService(session).list_for_user(user.id)
    return [_to_response(w) for w in watchlists]


@router.post(
    "",
    response_model=WatchlistResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a watchlist",
)
async def create_watchlist(
    session: DbSession,
    user: CurrentUser,
    payload: WatchlistCreateRequest,
) -> WatchlistResponse:
    """409 when the name is already taken — names are unique per user."""
    watchlist = await WatchlistService(session).create(
        user.id,
        name=payload.name,
        is_default=payload.is_default,
    )
    return _to_response(watchlist)


@router.get("/{watchlist_id}", response_model=WatchlistResponse, summary="Get a watchlist")
async def get_watchlist(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
) -> WatchlistResponse:
    watchlist = await WatchlistService(session).get_for_user(user.id, watchlist_id)
    return _to_response(watchlist)


@router.patch("/{watchlist_id}", response_model=WatchlistResponse, summary="Rename a watchlist")
async def update_watchlist(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
    payload: WatchlistUpdateRequest,
) -> WatchlistResponse:
    watchlist = await WatchlistService(session).update(
        user.id,
        watchlist_id,
        name=payload.name,
        is_default=payload.is_default,
    )
    return _to_response(watchlist)


@router.delete(
    "/{watchlist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a watchlist",
)
async def delete_watchlist(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
) -> Response:
    await WatchlistService(session).delete(user.id, watchlist_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{watchlist_id}/items",
    response_model=WatchlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a symbol to a watchlist",
)
async def add_item(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
    payload: WatchlistItemCreateRequest,
) -> WatchlistItemResponse:
    """409 if the symbol is already on the list; 404 if the ticker is unknown."""
    item = await WatchlistService(session).add_item(
        user.id,
        watchlist_id,
        ticker=payload.ticker,
        notes=payload.notes,
    )
    return WatchlistItemResponse.model_validate(item)


@router.patch(
    "/{watchlist_id}/items",
    response_model=list[WatchlistItemResponse],
    summary="Reorder a watchlist",
)
async def reorder_items(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
    payload: WatchlistReorderRequest,
) -> list[WatchlistItemResponse]:
    """Takes the complete ordering; a partial list is a 422."""
    items = await WatchlistService(session).reorder(user.id, watchlist_id, payload.item_ids)
    return [WatchlistItemResponse.model_validate(i) for i in items]


@router.delete(
    "/{watchlist_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a symbol from a watchlist",
)
async def remove_item(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
    item_id: uuid.UUID,
) -> Response:
    await WatchlistService(session).remove_item(user.id, watchlist_id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{watchlist_id}/quotes",
    response_model=WatchlistDetailResponse,
    summary="The quote grid for a watchlist",
)
async def watchlist_quotes(
    session: DbSession,
    user: CurrentUser,
    watchlist_id: uuid.UUID,
) -> WatchlistDetailResponse:
    """The endpoint the grid polls: one request, one joined read.

    Stale quotes are refreshed first so that polling this actually moves the
    prices, then the rows come back from a single join rather than a query per
    item. Refreshing is cache-first and bounded by the quote TTL, so polling
    faster than the TTL costs nothing upstream.
    """
    watchlists = WatchlistService(session)
    watchlist = await watchlists.get_for_user(user.id, watchlist_id)
    rows = await watchlists.rows_for(watchlist)

    # The joined read already carries whatever was cached; this tops up anything
    # past its TTL and hands back the refreshed rows, so there is no second join.
    refreshed = await MarketDataService(session).get_quotes(
        [symbol.ticker for _, symbol, _ in rows]
    )

    return WatchlistDetailResponse(
        watchlist=_to_response(watchlist),
        rows=[
            WatchlistRowResponse(
                item=WatchlistItemResponse.model_validate(item),
                symbol=SymbolResponse.model_validate(symbol),
                quote=(
                    QuoteResponse.from_row(symbol.ticker, current)
                    if (current := refreshed.get(symbol.ticker) or quote)
                    else None
                ),
            )
            for item, symbol, quote in rows
        ],
    )
