"""Aggregates every v1 route module into a single router.

New feature routers are registered here and nowhere else.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import auth, market_data, meta, watchlists

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(market_data.router)
api_router.include_router(meta.router)
api_router.include_router(watchlists.router)
