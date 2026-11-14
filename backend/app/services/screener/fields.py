"""The field registry — the one list of what a screen can name.

Every entry maps a key to a real SQLAlchemy expression, a unit, and the node
kinds the field may appear in. It has two consumers and no others:

* the compiler, which resolves a key to something it can put in a ``WHERE``;
* ``GET /screener/fields``, which serves the catalogue to the filter builder.

One source, so the UI cannot offer a field the compiler will reject, and adding
a field is one entry here rather than a change in three files. The frontend
deliberately does not keep its own copy — see the Phase 4 spec.

**Nothing here is built from a string at runtime.** ``FIELDS`` is a mapping of
literal keys to expressions constructed at import time; a request supplies a
key, and a key that is not in this mapping fails validation in
``app.schemas.screener`` before any service runs.

Three things about the shape are worth stating, because each looks like an
oversight otherwise:

* Some entries support **no** filter kinds at all — ``ticker`` and ``name``.
  They are in the registry because they are sortable and displayed, not because
  they are filterable; filtering on a ticker is what the symbol search is for.
* Derived fields such as ``pct_from_sma_50`` are ordinary entries whose
  expression is arithmetic rather than a column. They are what makes a
  comparison scale-free: ``close > sma_50`` is true for a stock a cent above its
  average, while ``pct_from_sma_50 >= 5`` is the screen someone meant.
* Every denominator in that arithmetic is wrapped in ``NULLIF(x, 0)``, so a zero
  divisor yields ``NULL`` rather than an error — which lands on exactly the
  null-does-not-match behaviour the rest of the phase relies on.

``is_active`` is not a field. A screen over delisted names is not something
anyone wants, and making it optional invites getting it wrong, so the service
filters on it unconditionally.

**There is one registry per data source.** ``FIELDS`` is the screener's,
reading ``daily_bars``, and end-of-day alerts use it unchanged.
``INTRADAY_FIELDS`` serves intraday alerts: the same registry with the bar
fields swapped for live ones from ``latest_quotes``, and every indicator kept.
So "price above its 200-day average" is a ``compare`` between a live price and
last night's average, and both are in ``price`` units. A registry per source,
rather than a flag on each entry, keeps "which fields exist here" one lookup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, SQLColumnExpression, cast, func

from app.core.enums import AssetType, FieldUnit, FilterKind
from app.models.market_data import DailyBar, DailyIndicator, LatestQuote
from app.models.symbol import Symbol

__all__ = [
    "FIELDS",
    "INTRADAY_FIELDS",
    "KNOWN_FIELDS",
    "FieldRegistry",
    "FieldSpec",
    "resolve",
]

_NUMERIC = frozenset({FilterKind.NUMERIC})
_NUMERIC_COMPARE = frozenset({FilterKind.NUMERIC, FilterKind.COMPARE})
_CATEGORY = frozenset({FilterKind.CATEGORY})
_DISPLAY_ONLY: frozenset[FilterKind] = frozenset()

#: Matches the storage type of every price column, so a derived percentage does
#: not come back at whatever precision the division happened to produce.
_RESULT = Numeric(18, 6)


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One screenable — or merely sortable — column."""

    key: str
    label: str
    unit: FieldUnit
    kinds: frozenset[FilterKind]
    expression: SQLColumnExpression[Any]
    #: Permitted values for a ``category`` field. ``None`` means "ask the
    #: database": the catalogue fills it with a ``DISTINCT`` over the live
    #: universe, so the builder offers what actually exists rather than what a
    #: constant once claimed. Non-category fields leave it empty.
    category_values: tuple[str, ...] | None = ()

    def supports(self, kind: FilterKind) -> bool:
        return kind in self.kinds


def _percent_of(
    numerator: SQLColumnExpression[Any], denominator: SQLColumnExpression[Any]
) -> SQLColumnExpression[Any]:
    """``(numerator / denominator) * 100``, null-safe on a zero denominator.

    ``NULLIF`` rather than a ``CASE``: a zero divisor is not a match of value
    zero, it is an absence of an answer, and ``NULL`` is how this project says
    that. It also keeps the expression a single term, which matters because
    these get nested inside the compiled ``WHERE``.
    """
    return cast(numerator / func.nullif(denominator, 0) * 100, _RESULT)


def _spec(
    key: str,
    label: str,
    unit: FieldUnit,
    kinds: frozenset[FilterKind],
    expression: SQLColumnExpression[Any],
    category_values: tuple[str, ...] | None = (),
) -> tuple[str, FieldSpec]:
    return key, FieldSpec(
        key=key,
        label=label,
        unit=unit,
        kinds=kinds,
        expression=expression,
        category_values=category_values,
    )


#: Ordered deliberately: the catalogue is served in this order and the filter
#: builder's dropdown shows it unchanged, so the grouping is the UI's grouping.
FIELDS: dict[str, FieldSpec] = dict(
    [
        # -------------------------------------------------------------- symbol
        _spec("ticker", "Ticker", FieldUnit.TEXT, _DISPLAY_ONLY, Symbol.ticker),
        _spec("name", "Name", FieldUnit.TEXT, _DISPLAY_ONLY, Symbol.name),
        _spec(
            "exchange",
            "Exchange",
            FieldUnit.TEXT,
            _CATEGORY,
            Symbol.exchange,
            category_values=None,
        ),
        _spec(
            "asset_type",
            "Asset type",
            FieldUnit.TEXT,
            _CATEGORY,
            Symbol.asset_type,
            category_values=tuple(a.value for a in AssetType),
        ),
        # ---------------------------------------------------------------- bars
        _spec("close", "Close", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyBar.close),
        _spec("open", "Open", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyBar.open),
        _spec("high", "High", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyBar.high),
        _spec("low", "Low", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyBar.low),
        _spec("volume", "Volume", FieldUnit.SHARES, _NUMERIC_COMPARE, DailyBar.volume),
        # ---------------------------------------------------------- indicators
        _spec("sma_20", "SMA 20", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.sma_20),
        _spec("sma_50", "SMA 50", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.sma_50),
        _spec("sma_200", "SMA 200", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.sma_200),
        _spec("ema_12", "EMA 12", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.ema_12),
        _spec("ema_26", "EMA 26", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.ema_26),
        _spec(
            "high_52w", "52-week high", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.high_52w
        ),
        _spec("low_52w", "52-week low", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.low_52w),
        _spec("atr_14", "ATR 14", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.atr_14),
        _spec("macd", "MACD", FieldUnit.PRICE, _NUMERIC_COMPARE, DailyIndicator.macd),
        _spec(
            "macd_signal",
            "MACD signal",
            FieldUnit.PRICE,
            _NUMERIC_COMPARE,
            DailyIndicator.macd_signal,
        ),
        _spec(
            "macd_histogram",
            "MACD histogram",
            FieldUnit.PRICE,
            _NUMERIC_COMPARE,
            DailyIndicator.macd_histogram,
        ),
        _spec(
            "volume_sma_20",
            "Volume SMA 20",
            FieldUnit.SHARES,
            _NUMERIC_COMPARE,
            DailyIndicator.volume_sma_20,
        ),
        # RSI is numeric-only on purpose: it is bounded 0-100 and shares that
        # scale with nothing else here, so every `compare` it could appear in
        # would be a unit mismatch anyway.
        _spec("rsi_14", "RSI 14", FieldUnit.RATIO, _NUMERIC, DailyIndicator.rsi_14),
        _spec(
            "change_percent_1d",
            "Change % (1d)",
            FieldUnit.PERCENT,
            _NUMERIC,
            DailyIndicator.change_percent_1d,
        ),
        # ------------------------------------------------------------- derived
        _spec(
            "pct_from_sma_50",
            "% from SMA 50",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(DailyBar.close - DailyIndicator.sma_50, DailyIndicator.sma_50),
        ),
        _spec(
            "pct_from_sma_200",
            "% from SMA 200",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(DailyBar.close - DailyIndicator.sma_200, DailyIndicator.sma_200),
        ),
        # Signed, and negative below the high — so "within 5% of the 52-week
        # high" reads as `>= -5`, and the number sorts the way a distance from a
        # ceiling should. The column label says "% from" rather than "% below"
        # for the same reason; a positive-distance-below reading would make
        # `pct_from_sma_50` and this one disagree about which way is up.
        _spec(
            "pct_from_52w_high",
            "% from 52-week high",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(DailyBar.close - DailyIndicator.high_52w, DailyIndicator.high_52w),
        ),
        _spec(
            "pct_from_52w_low",
            "% from 52-week low",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(DailyBar.close - DailyIndicator.low_52w, DailyIndicator.low_52w),
        ),
        _spec(
            "atr_percent",
            "ATR % of close",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(DailyIndicator.atr_14, DailyBar.close),
        ),
    ]
)

type FieldRegistry = Mapping[str, FieldSpec]

#: The bar fields an intraday alert cannot use: there is no bar until the close.
_BAR_ONLY = frozenset(
    {
        "close",
        "open",
        "high",
        "low",
        "volume",
        "change_percent_1d",
        "pct_from_sma_50",
        "pct_from_sma_200",
        "pct_from_52w_high",
        "pct_from_52w_low",
        "atr_percent",
    }
)

_LIVE_FIELDS: dict[str, FieldSpec] = dict(
    [
        _spec("price", "Price", FieldUnit.PRICE, _NUMERIC_COMPARE, LatestQuote.price),
        _spec("day_open", "Day open", FieldUnit.PRICE, _NUMERIC_COMPARE, LatestQuote.day_open),
        _spec("day_high", "Day high", FieldUnit.PRICE, _NUMERIC_COMPARE, LatestQuote.day_high),
        _spec("day_low", "Day low", FieldUnit.PRICE, _NUMERIC_COMPARE, LatestQuote.day_low),
        _spec(
            "volume",
            "Volume (so far today)",
            FieldUnit.SHARES,
            _NUMERIC_COMPARE,
            LatestQuote.volume,
        ),
        _spec(
            "change_percent",
            "Change % (today)",
            FieldUnit.PERCENT,
            _NUMERIC,
            LatestQuote.change_percent,
        ),
    ]
)

_LIVE_DERIVED: dict[str, FieldSpec] = dict(
    [
        _spec(
            "pct_from_sma_50",
            "% from SMA 50",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(LatestQuote.price - DailyIndicator.sma_50, DailyIndicator.sma_50),
        ),
        _spec(
            "pct_from_sma_200",
            "% from SMA 200",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(LatestQuote.price - DailyIndicator.sma_200, DailyIndicator.sma_200),
        ),
        _spec(
            "pct_from_52w_high",
            "% from 52-week high",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(LatestQuote.price - DailyIndicator.high_52w, DailyIndicator.high_52w),
        ),
        _spec(
            "pct_from_52w_low",
            "% from 52-week low",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(LatestQuote.price - DailyIndicator.low_52w, DailyIndicator.low_52w),
        ),
        _spec(
            "atr_percent",
            "ATR % of price",
            FieldUnit.PERCENT,
            _NUMERIC,
            _percent_of(DailyIndicator.atr_14, LatestQuote.price),
        ),
    ]
)

#: For intraday alerts: symbol fields, then live quote fields, then every
#: indicator (joined at the shared ``as_of``), then the derived fields
#: recomputed against the live price.
INTRADAY_FIELDS: dict[str, FieldSpec] = {
    **{k: v for k, v in FIELDS.items() if k in ("ticker", "name", "exchange", "asset_type")},
    **_LIVE_FIELDS,
    **{
        k: v
        for k, v in FIELDS.items()
        if k not in _BAR_ONLY and k not in ("ticker", "name", "exchange", "asset_type")
    },
    **_LIVE_DERIVED,
}

#: Every key any registry defines, for validating a tree's *structure* before
#: the caller checks it against the registry it will actually run in. A key
#: that two registries share has the same unit and filter kinds in both, which
#: ``tests/unit/test_screener_dsl.py`` asserts, so either spec answers those
#: questions.
KNOWN_FIELDS: dict[str, FieldSpec] = {**INTRADAY_FIELDS, **FIELDS}

#: What every result row carries whether or not the run mentioned it, so the
#: table is never a column of tickers with nothing beside it.
CORE_VALUE_FIELDS: tuple[str, ...] = ("close", "volume", "change_percent_1d")

#: Sorting defaults to ticker: stable, and not a claim about which match is best.
DEFAULT_SORT_FIELD = "ticker"


def resolve(key: str, registry: FieldRegistry = FIELDS) -> FieldSpec:
    """Look up a field, or raise ``KeyError``.

    Callers reach this only with a key that already passed schema validation
    against ``registry``, so the ``KeyError`` is a programming error rather than
    a user error — which is why it is not a domain exception.
    """
    return registry[key]


def decimal_or_none(value: Any) -> Decimal | None:
    """Coerce a row value to ``Decimal`` for the response.

    ``volume`` is a ``BIGINT`` and everything else is ``NUMERIC``; the response
    carries them all as strings, so they converge here rather than at the
    serializer.
    """
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(value)
