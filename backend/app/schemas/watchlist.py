"""API representations of watchlists and their contents."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.market_data import QuoteResponse, SymbolResponse


class WatchlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    is_default: bool = False


class WatchlistUpdateRequest(BaseModel):
    """Every field optional — this is a PATCH, so absent means "leave alone"."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_default: bool | None = None


class WatchlistItemCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=500)


class WatchlistReorderRequest(BaseModel):
    """The full ordering, not a moved pair.

    A partial reorder has no well-defined answer for the items it leaves out,
    and the drag-and-drop UI already knows the complete order it wants.
    """

    item_ids: list[uuid.UUID] = Field(min_length=1)


class WatchlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol_id: uuid.UUID
    position: int
    notes: str | None
    created_at: datetime


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    item_count: int = 0


class WatchlistRowResponse(BaseModel):
    """One line of the quote grid: the item, its symbol, and its latest price.

    Flattened into a single object per row on purpose — the grid renders rows,
    and making the client stitch three collections together is how N+1 gets
    reinvented on the frontend.
    """

    item: WatchlistItemResponse
    symbol: SymbolResponse
    quote: QuoteResponse | None = None


class WatchlistDetailResponse(BaseModel):
    watchlist: WatchlistResponse
    rows: list[WatchlistRowResponse]
