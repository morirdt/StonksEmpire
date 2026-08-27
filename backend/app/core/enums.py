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

    **Exactly three, and that is not an accident.** The validated overlay
    palette has three slots, and the chart assigns colour by indicator identity
    rather than by the order lines were switched on — otherwise turning one off
    repaints the others. Those two facts together mean the number of overlay
    identities cannot exceed the number of slots, or some pair is permanently
    the same colour. The stored EMAs are deliberately absent for that reason:
    they exist to feed MACD, which has its own pane.
    """

    SMA_20 = "sma_20"
    SMA_50 = "sma_50"
    SMA_200 = "sma_200"


class Oscillator(StrEnum):
    """Indicators that get a pane of their own, because their scale is not price."""

    RSI_14 = "rsi_14"
    MACD = "macd"


class FilterKind(StrEnum):
    """The node kinds a screener filter tree is built from.

    Also the vocabulary the field registry uses to say what a field may appear
    in: ``close`` is ``numeric`` and ``compare``, ``exchange`` is ``category``
    only, and ``ticker`` is none of them — it is sortable and displayed, but
    filtering on it is what the symbol search is for.
    """

    NUMERIC = "numeric"
    COMPARE = "compare"
    CATEGORY = "category"
    GROUP = "group"


class NumericOperator(StrEnum):
    """Comparisons between a field and a literal value.

    ``BETWEEN`` is here and not on ``CompareOperator`` because it takes a second
    operand, which only makes sense against literals.
    """

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"
    BETWEEN = "between"


class CompareOperator(StrEnum):
    """Comparisons between two fields.

    Deliberately a separate enum rather than a subset of ``NumericOperator``:
    ``between`` has no meaning here, and an enum that has to be range-checked
    after parsing is not doing its job.
    """

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"


class CategoryOperator(StrEnum):
    """Set membership, for fields whose values are labels rather than numbers."""

    IN = "in"
    NOT_IN = "not_in"


class GroupOperator(StrEnum):
    """How a group combines its children. Nesting is what supplies precedence."""

    AND = "and"
    OR = "or"


class FieldUnit(StrEnum):
    """What a screener field is measured in.

    This exists to stop ``close > volume``, which parses fine and means nothing.
    A ``compare`` node is rejected unless both sides share a unit, so the
    registry has to carry one per field.
    """

    PRICE = "price"
    SHARES = "shares"
    PERCENT = "percent"
    #: RSI and friends: bounded 0-100, comparable only with each other.
    RATIO = "ratio"
    #: Tickers, names, exchanges — never an operand of an arithmetic comparison.
    TEXT = "text"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"
