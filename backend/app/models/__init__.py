"""SQLAlchemy models.

Every model module MUST be imported here. ``alembic/env.py`` imports this
package to populate ``Base.metadata``; a model that is not reachable from this
file is invisible to autogenerate, and Alembic will emit a migration that drops
its table.
"""

from __future__ import annotations

from app.models.chart_preferences import UserChartPreferences
from app.models.market_data import DailyBar, DailyIndicator, LatestQuote
from app.models.refresh_token import RefreshToken
from app.models.screener import ScreenerPreset
from app.models.symbol import Symbol
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem

__all__: list[str] = [
    "DailyBar",
    "DailyIndicator",
    "LatestQuote",
    "RefreshToken",
    "ScreenerPreset",
    "Symbol",
    "User",
    "UserChartPreferences",
    "Watchlist",
    "WatchlistItem",
]
