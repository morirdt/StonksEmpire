"""Enumerations shared across layers.

These live in ``core`` rather than beside a model because both the ORM and the
provider dataclasses need them, and ``app/integrations`` must not import from
``app/models`` — the point of the provider seam is that it knows nothing about
persistence.

Per ``CLAUDE.md`` these are stored as ``VARCHAR`` plus a CHECK constraint, never
a native Postgres enum: the value sets here change often and ``ALTER TYPE`` is
painful.
"""

from __future__ import annotations

from enum import StrEnum


class AssetType(StrEnum):
    """What kind of instrument a symbol is.

    Deliberately coarse. The distinctions that matter downstream are "can it be
    charted like an equity" and "is it a fund", not the full taxonomy a provider
    reports.
    """

    COMMON_STOCK = "common_stock"
    ETF = "etf"
    ADR = "adr"
    REIT = "reit"
    PREFERRED = "preferred"
    OTHER = "other"


class ChartRange(StrEnum):
    """A window of history, expressed the way a chart toolbar expresses it.

    Shared by the bars and indicators query strings and by the stored chart
    preference, which is why one enum serves all three rather than each layer
    inventing its own string set.

    ``MAX`` means "everything stored", bounded by the row cap in
    ``app.services.chart_service`` rather than by a date.
    """

    ONE_MONTH = "1M"
    THREE_MONTHS = "3M"
    SIX_MONTHS = "6M"
    ONE_YEAR = "1Y"
    TWO_YEARS = "2Y"
    MAX = "MAX"


class PriceOverlay(StrEnum):
    """Indicators drawn on the price pane, in price units.

    Only moving averages qualify: an overlay has to share the candles' y-scale
    to mean anything, which rules out RSI (0-100) and MACD (centred on zero).
    Each value is a column of ``daily_indicators``.
    """

    SMA_20 = "sma_20"
    SMA_50 = "sma_50"
    SMA_200 = "sma_200"
    EMA_12 = "ema_12"
    EMA_26 = "ema_26"


class Oscillator(StrEnum):
    """Indicators that get a pane of their own, because their scale is not price."""

    RSI_14 = "rsi_14"
    MACD = "macd"
