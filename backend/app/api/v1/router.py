"""Aggregates every v1 route module into a single router.

New feature routers are registered here and nowhere else.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    auth,
    chart_preferences,
    market_data,
    meta,
    screener,
    watchlists,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(chart_preferences.router)
api_router.include_router(market_data.router)
api_router.include_router(meta.router)
api_router.include_router(screener.router)
api_router.include_router(watchlists.router)
