"""The filter DSL, and the wire shapes around it.

This module is the security boundary of the whole phase. The screener turns
user-supplied JSON into SQL, which is the first place in this project where user
input decides a query's *shape* rather than just its parameters, and the rule
that makes that safe lives here:

> **No user-supplied string ever reaches SQL.** A filter names a field by key;
> the key is looked up in ``app.services.screener.fields`` and resolved to a
> real SQLAlchemy expression. An unknown key, an unknown operator, and a
> unit-mismatched comparison are each a 422 raised *here*, before any service
> runs. There is no branch in which a field name is interpolated into a query.

Two other things are enforced at this layer and nowhere else:

**Values cross the wire as strings.** Prices are ``NUMERIC``/``Decimal``
everywhere in this project and must not round-trip through a float — a screen
for ``close >= 100.10`` that compares against ``100.09999999999999`` is exactly
the class of bug the convention exists to prevent. A JSON number is therefore
rejected outright rather than quietly coerced, with a message saying to quote
it.

**Caps.** A recursive schema with no ceiling is a denial-of-service shaped like
a feature, so depth, node count, and ``values`` length are all bounded by a
validator that walks the tree once. The limit is a 422; it must never be an
``OperationalError`` from a query nobody could have executed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic import ValidationError as PydanticValidationError

from app.core.enums import (
    CategoryOperator,
    CompareOperator,
    FieldUnit,
    FilterKind,
    GroupOperator,
    NumericOperator,
    SortDirection,
)
from app.core.exceptions import ValidationError
from app.services.screener.fields import DEFAULT_SORT_FIELD, FIELDS, KNOWN_FIELDS, FieldRegistry

# ---------------------------------------------------------------------- caps

#: Group nesting, counting the root as one. A leaf does not add a level, so
#: ``a AND (b OR c)`` is depth 2 and this permits one more level below that.
#: Deeper than this is unbuildable in the UI and unreadable in JSON.
MAX_DEPTH = 3
#: Total nodes, groups included. A 25-predicate screen is already past useful.
MAX_NODES = 25
#: Longer than the list of exchanges.
MAX_CATEGORY_VALUES = 50
#: Category values are labels, not free text.
MAX_CATEGORY_VALUE_LENGTH = 40

#: The row cap on a run. There is no pagination on purpose: a screen returning
#: 412 of 530 wants narrowing, not a second page, and ``total_matched`` is what
#: says so.
MAX_ROW_LIMIT = 200
DEFAULT_ROW_LIMIT = 100


def _reject_json_number(value: Any) -> Any:
    """Numeric operands must arrive as strings.

    ``bool`` is checked first because it is an ``int`` in Python and a stray
    ``true`` would otherwise be read as ``1``.
    """
    if isinstance(value, bool | int | float):
        raise ValueError(
            "Send numeric values as strings, not JSON numbers — "
            "a JSON number is parsed as a float and loses precision."
        )
    return value


#: NUMERIC(18, 6) is what these are compared against, so an operand wider than
#: the column is rejected here rather than by a Postgres overflow at run time.
FilterValue = Annotated[
    Decimal,
    BeforeValidator(_reject_json_number),
    Field(max_digits=18, decimal_places=6, allow_inf_nan=False),
    # Declared as a plain string in the OpenAPI schema, because that is what the
    # endpoint actually accepts. Pydantic's default for Decimal is
    # `anyOf: [number, string]`, which would generate a TypeScript client
    # offering a `number` the validator above rejects.
    WithJsonSchema(
        {"type": "string", "description": "A decimal, as a string — never a JSON number."}
    ),
]


def _known_field(key: str) -> str:
    """A key some registry defines. Which registry is the caller's to check.

    Nodes validate structure, units, and filter kinds against every known
    field. Each entry point then checks that the tree uses only its own
    registry (:func:`require_registry`), so ``price`` is valid in an intraday
    alert and is still a 422 in a screen.
    """
    if key not in KNOWN_FIELDS:
        raise ValueError(f"Unknown field {key!r}. See GET /screener/fields for the catalogue.")
    return key


FieldKey = Annotated[str, BeforeValidator(_known_field)]


def _require_kind(key: str, kind: FilterKind) -> None:
    spec = KNOWN_FIELDS[key]
    if not spec.supports(kind):
        supported = ", ".join(sorted(k.value for k in spec.kinds)) or "no filters"
        raise ValueError(
            f"Field {key!r} does not support {kind.value} filters (supports: {supported})."
        )


# --------------------------------------------------------------------- nodes


class NumericFilter(BaseModel):
    """``column <op> :value`` — the ordinary case."""

    kind: Literal[FilterKind.NUMERIC] = FilterKind.NUMERIC
    field: FieldKey
    op: NumericOperator
    value: FilterValue
    #: The upper bound, required by ``between`` and forbidden by everything else.
    value2: FilterValue | None = None

    @field_validator("field", mode="after")
    @classmethod
    def _supports_numeric(cls, key: str) -> str:
        _require_kind(key, FilterKind.NUMERIC)
        return key

    @model_validator(mode="after")
    def _check_bounds(self) -> NumericFilter:
        if self.op is NumericOperator.BETWEEN:
            if self.value2 is None:
                raise ValueError("A 'between' filter needs both 'value' and 'value2'.")
            if self.value2 < self.value:
                raise ValueError("A 'between' filter needs 'value2' to be at least 'value'.")
        elif self.value2 is not None:
            raise ValueError(f"'value2' is only meaningful for 'between', not {self.op.value!r}.")
        return self


class CompareFilter(BaseModel):
    """``column <op> column`` — the point of the whole feature.

    "Price above its 200-day average" is a comparison between two columns, not
    between a column and a number. A DSL that only supports ``field op literal``
    looks complete and cannot express it.

    Both sides must share a unit. ``close > volume`` parses fine and means
    nothing, so a mismatch is a 422 naming both units rather than a screen that
    silently returns whatever the numbers happen to do.
    """

    kind: Literal[FilterKind.COMPARE] = FilterKind.COMPARE
    left: FieldKey
    op: CompareOperator
    right: FieldKey

    @field_validator("left", "right", mode="after")
    @classmethod
    def _supports_compare(cls, key: str) -> str:
        _require_kind(key, FilterKind.COMPARE)
        return key

    @model_validator(mode="after")
    def _units_match(self) -> CompareFilter:
        left, right = KNOWN_FIELDS[self.left].unit, KNOWN_FIELDS[self.right].unit
        if left is not right:
            raise ValueError(
                f"Cannot compare {self.left!r} ({left.value}) with "
                f"{self.right!r} ({right.value}) — the units differ."
            )
        return self


class CategoryFilter(BaseModel):
    """``column IN (…)`` / ``NOT IN (…)`` over a label field."""

    kind: Literal[FilterKind.CATEGORY] = FilterKind.CATEGORY
    field: FieldKey
    op: CategoryOperator
    values: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_CATEGORY_VALUE_LENGTH)]],
        Field(min_length=1, max_length=MAX_CATEGORY_VALUES),
    ]

    @field_validator("field", mode="after")
    @classmethod
    def _supports_category(cls, key: str) -> str:
        _require_kind(key, FilterKind.CATEGORY)
        return key

    @model_validator(mode="after")
    def _values_are_permitted(self) -> CategoryFilter:
        """Reject a value the field cannot take — where the field has a fixed set.

        ``asset_type`` does; ``exchange`` does not, because its values are data
        rather than schema and the catalogue reads them from the universe. An
        unrecognised exchange is simply a screen that matches nothing, which is
        the honest answer.
        """
        permitted = KNOWN_FIELDS[self.field].category_values
        if permitted is None:
            return self
        unknown = [v for v in self.values if v not in permitted]
        if unknown:
            raise ValueError(
                f"Unknown {self.field} value(s): {', '.join(sorted(unknown))}. "
                f"Permitted: {', '.join(permitted)}."
            )
        return self


class GroupFilter(BaseModel):
    """``AND`` / ``OR`` over children. Nesting is what supplies precedence."""

    kind: Literal[FilterKind.GROUP] = FilterKind.GROUP
    op: GroupOperator
    children: Annotated[list[FilterNode], Field(min_length=1, max_length=MAX_NODES)]


FilterNode = Annotated[
    NumericFilter | CompareFilter | CategoryFilter | GroupFilter,
    Field(discriminator="kind"),
]

GroupFilter.model_rebuild()


# ---------------------------------------------------------------------- caps


def _walk(node: Any, depth: int) -> tuple[int, int]:
    """Return ``(nodes, max_depth)`` for the subtree rooted at ``node``.

    One pass, because the caps have to be cheap enough to run before anything
    else does — the whole point is that an oversized tree costs a validation
    error rather than a query.
    """
    if not isinstance(node, GroupFilter):
        return 1, depth

    nodes = 1
    deepest = depth
    for child in node.children:
        child_nodes, child_depth = _walk(
            child, depth + 1 if isinstance(child, GroupFilter) else depth
        )
        nodes += child_nodes
        deepest = max(deepest, child_depth)
    return nodes, deepest


def enforce_caps(node: Any) -> Any:
    nodes, depth = _walk(node, 1 if isinstance(node, GroupFilter) else 0)
    if depth > MAX_DEPTH:
        raise ValueError(f"Filter tree is nested {depth} groups deep; the limit is {MAX_DEPTH}.")
    if nodes > MAX_NODES:
        raise ValueError(f"Filter tree has {nodes} nodes; the limit is {MAX_NODES}.")
    return node


def tree_fields(node: Any) -> list[str]:
    """Every field key a validated tree names, in first-seen order."""
    seen: dict[str, None] = {}

    def walk(current: Any) -> None:
        if isinstance(current, NumericFilter | CategoryFilter):
            seen.setdefault(current.field, None)
        elif isinstance(current, CompareFilter):
            seen.setdefault(current.left, None)
            seen.setdefault(current.right, None)
        elif isinstance(current, GroupFilter):
            for child in current.children:
                walk(child)

    walk(node)
    return list(seen)


def require_registry(node: Any, registry: FieldRegistry, *, catalogue: str) -> Any:
    """Reject a tree that names a field ``registry`` does not have.

    ``catalogue`` is the endpoint that lists this registry, so the message can
    say where to look.
    """
    foreign = [key for key in tree_fields(node) if key not in registry]
    if foreign:
        raise ValueError(
            f"Field {foreign[0]!r} is not available here. See {catalogue} for the catalogue."
        )
    return node


def _screener_tree(node: Any) -> Any:
    return require_registry(enforce_caps(node), FIELDS, catalogue="GET /screener/fields")


# -------------------------------------------------------------------- sorting


class ScreenerSort(BaseModel):
    field: FieldKey = DEFAULT_SORT_FIELD
    direction: SortDirection = SortDirection.ASC

    @field_validator("field", mode="after")
    @classmethod
    def _screenable(cls, key: str) -> str:
        if key not in FIELDS:
            raise ValueError(f"Field {key!r} is not available here. See GET /screener/fields.")
        return key


def _default_sort() -> ScreenerSort:
    return ScreenerSort()


class ScreenerRunRequest(BaseModel):
    """An ad-hoc screen.

    ``POST`` for a read: a filter tree does not fit in a query string legibly,
    and URL-encoding JSON into a ``GET`` trades a readable body for an
    unreadable URL and a length limit. The lost HTTP caching is not a cost here
    — the underlying data changes once a day.
    """

    filters: FilterNode
    sort: ScreenerSort = Field(default_factory=_default_sort)
    limit: int = Field(default=DEFAULT_ROW_LIMIT, ge=1, le=MAX_ROW_LIMIT)

    @field_validator("filters", mode="after")
    @classmethod
    def _within_caps(cls, node: Any) -> Any:
        return _screener_tree(node)


# ------------------------------------------------------------------ catalogue


class ScreenerFieldResponse(BaseModel):
    """One entry of the catalogue the filter builder is generated from."""

    key: str
    label: str
    unit: FieldUnit
    kinds: list[FilterKind]
    #: Populated for ``category`` fields, empty otherwise.
    values: list[str] = Field(default_factory=list)


class ScreenerFieldsResponse(BaseModel):
    fields: list[ScreenerFieldResponse]
    #: Every key that may appear in ``sort.field`` — which includes the
    #: display-only ones, because a column you can see is a column you can sort.
    sort_fields: list[str]
    default_sort: ScreenerSort
    max_limit: int = MAX_ROW_LIMIT
    max_depth: int = MAX_DEPTH
    max_nodes: int = MAX_NODES


# -------------------------------------------------------------------- results


class ScreenerColumn(BaseModel):
    """A column of the results table, in display order."""

    key: str
    label: str
    unit: FieldUnit


class ScreenerRow(BaseModel):
    """One match: the symbol, plus the numbers that caused it to match.

    The identity is flat and the numbers are a map, because which numbers are
    present depends on the run — a row carries the fixed core plus every field
    the filters or the sort referenced, so a user can see *why* a symbol is
    there without opening it. ``columns`` on the response says what to render
    and in what order.

    Every value is a ``Decimal``, so it crosses the wire as a string and is
    parsed only at the point of display. See ``CLAUDE.md``.
    """

    ticker: str
    name: str
    exchange: str | None
    asset_type: str
    values: dict[str, Decimal | None]


class ScreenerRunResponse(BaseModel):
    """The result of one run.

    ``as_of`` is ``null`` only when ``daily_bars`` is empty — a fresh clone that
    has not been backfilled. The UI has a distinct empty state for it, because
    "the universe is empty" would otherwise be diagnosed as "nothing matched".

    ``total_matched`` is the count **before** the row cap, so the UI can say
    "412 matches, showing 100" — which is the signal that a screen is too loose.
    """

    as_of: date | None
    universe_size: int
    total_matched: int
    columns: list[ScreenerColumn]
    rows: list[ScreenerRow]


# -------------------------------------------------------------------- presets


class ScreenerPresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: FilterNode
    sort: ScreenerSort = Field(default_factory=_default_sort)

    @field_validator("filters", mode="after")
    @classmethod
    def _within_caps(cls, node: Any) -> Any:
        return _screener_tree(node)


class ScreenerPresetUpdateRequest(BaseModel):
    """A patch: everything is optional, and an omitted field is left alone."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    filters: FilterNode | None = None
    sort: ScreenerSort | None = None

    @field_validator("filters", mode="after")
    @classmethod
    def _within_caps(cls, node: Any) -> Any:
        return None if node is None else _screener_tree(node)


class ScreenerPresetSummary(BaseModel):
    """What the list page shows — deliberately **without** the filter tree.

    A stored tree can go stale: if a later phase removes an indicator column, a
    preset naming it no longer validates. The list must still load, so it never
    parses ``filters``. Opening or running the preset is where validation
    happens, and where a stale one fails with a message naming the field.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime


class ScreenerPresetResponse(ScreenerPresetSummary):
    filters: FilterNode
    sort: ScreenerSort


class ScreenerPresetRunRequest(BaseModel):
    """Overrides for a saved preset's run. The saved filters are not negotiable."""

    limit: int = Field(default=DEFAULT_ROW_LIMIT, ge=1, le=MAX_ROW_LIMIT)


#: Parses a tree that came out of the database rather than off the wire.
_TREE_ADAPTER: TypeAdapter[Any] = TypeAdapter(FilterNode)


def parse_stored_filters(
    raw: Any,
    registry: FieldRegistry = FIELDS,
    *,
    catalogue: str = "GET /screener/fields",
    what: str = "preset",
) -> FilterNode:
    """Re-validate a stored filter tree, or raise a 422 that says why.

    A preset's tree lives in ``JSONB``, so the database guarantees nothing about
    it, and a later phase that removes an indicator column leaves every preset
    naming it unparseable. That is a 422 with the offending field in the message
    — never a 500, and never a query assembled from a column that no longer
    exists. The list endpoint deliberately does not call this, so one broken
    preset cannot take the whole screener page down with it.
    """
    try:
        tree = enforce_caps(_TREE_ADAPTER.validate_python(raw))
        return cast(FilterNode, require_registry(tree, registry, catalogue=catalogue))
    except (PydanticValidationError, ValueError) as exc:
        raise ValidationError(
            f"This {what}'s filters are no longer valid — a field or operator it "
            f"names has changed. ({_first_message(exc)})"
        ) from exc


def parse_tree(raw: Any, registry: FieldRegistry, *, catalogue: str) -> FilterNode:
    """Validate a tree arriving on the wire against ``registry``.

    For request models whose registry depends on another field, such as an
    alert's ``kind``. Raises ``ValueError``, which Pydantic reports as a 422 at
    the field that called it.
    """
    try:
        tree = enforce_caps(_TREE_ADAPTER.validate_python(raw))
    except PydanticValidationError as exc:
        raise ValueError(_first_message(exc)) from exc
    return cast(FilterNode, require_registry(tree, registry, catalogue=catalogue))


def _first_message(exc: Exception) -> str:
    if isinstance(exc, PydanticValidationError) and exc.errors():
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        return f"{location}: {first.get('msg', '')}" if location else str(first.get("msg", ""))
    return str(exc)
