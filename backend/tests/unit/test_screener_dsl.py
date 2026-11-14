"""The filter DSL and its compiler, tested without a database.

This is the one genuinely hard, genuinely pure piece of Phase 4: a compiler that
turns user-supplied JSON into SQL. It is tested directly, before and apart from
any route, because everything that makes it dangerous — an unknown field, an
oversized tree, a comparison between incompatible units — is decided by pure
code that never needs a connection to be wrong.

Two layers, and the split matters:

* **The schema layer** rejects. An unknown field, an unknown operator, a
  unit-mismatched ``compare``, and a tree past any of the three caps are each a
  ``ValidationError`` raised here — which FastAPI turns into a 422 before the
  service is ever called. ``tests/integration/test_screener.py`` proves the
  status code; this file proves the rejection.
* **The compiler** translates. Given a tree the schema accepted, it produces a
  SQLAlchemy expression, and the assertions below are about the *rendered SQL*
  rather than about rows — which is what lets them run in milliseconds and cover
  every operator rather than a representative sample.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.schemas.screener import (
    MAX_CATEGORY_VALUES,
    MAX_DEPTH,
    MAX_NODES,
    ScreenerRunRequest,
    parse_stored_filters,
)
from app.services.screener.compiler import compile_filters, referenced_fields
from app.services.screener.fields import FIELDS


def _tree(payload: dict) -> object:
    """Validate a tree the way a request would, and hand back the root node."""
    return ScreenerRunRequest.model_validate({"filters": payload}).filters


def _sql(payload: dict) -> str:
    """The rendered SQL for a tree, with literals inlined so it can be read.

    ``literal_binds`` is a test affordance and nothing more — the real query
    binds parameters. It is used here because an assertion against
    ``:param_1`` proves nothing about which number was compared.
    """
    expression = compile_filters(_tree(payload))  # type: ignore[arg-type]
    return str(
        expression.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _numeric(field: str = "close", op: str = "gt", value: str = "100") -> dict:
    return {"kind": "numeric", "field": field, "op": op, "value": value}


def _group(op: str, *children: dict) -> dict:
    return {"kind": "group", "op": op, "children": list(children)}


def _nest(depth: int) -> dict:
    """A chain of groups ``depth`` deep, with one leaf at the bottom."""
    node = _numeric()
    for _ in range(depth):
        node = _group("and", node)
    return node


# ------------------------------------------------------------------ operators


@pytest.mark.parametrize(
    ("op", "rendered"),
    [
        ("gt", "daily_bars.close > 100"),
        ("gte", "daily_bars.close >= 100"),
        ("lt", "daily_bars.close < 100"),
        ("lte", "daily_bars.close <= 100"),
        ("eq", "daily_bars.close = 100"),
        ("neq", "daily_bars.close != 100"),
    ],
)
def test_every_numeric_operator_renders_its_comparison(op: str, rendered: str) -> None:
    assert _sql(_numeric(op=op)) == rendered


def test_between_renders_both_bounds() -> None:
    sql = _sql(
        {"kind": "numeric", "field": "rsi_14", "op": "between", "value": "30", "value2": "70"}
    )

    assert sql == "daily_indicators.rsi_14 BETWEEN 30 AND 70"


def test_between_needs_an_upper_bound() -> None:
    with pytest.raises(ValidationError, match="value2"):
        _tree({"kind": "numeric", "field": "rsi_14", "op": "between", "value": "30"})


def test_between_rejects_an_inverted_range() -> None:
    """An inverted range matches nothing, which reads as a bug in the data."""
    with pytest.raises(ValidationError, match="at least"):
        _tree(
            {
                "kind": "numeric",
                "field": "rsi_14",
                "op": "between",
                "value": "70",
                "value2": "30",
            }
        )


def test_value2_is_rejected_outside_between() -> None:
    with pytest.raises(ValidationError, match="only meaningful"):
        _tree({"kind": "numeric", "field": "close", "op": "gt", "value": "1", "value2": "2"})


def test_category_renders_in_and_not_in() -> None:
    values = ["common_stock", "etf"]
    assert "IN ('common_stock', 'etf')" in _sql(
        {"kind": "category", "field": "asset_type", "op": "in", "values": values}
    )
    assert "NOT IN ('common_stock', 'etf')" in _sql(
        {"kind": "category", "field": "asset_type", "op": "not_in", "values": values}
    )


# -------------------------------------------------------------------- compare


def test_compare_puts_two_columns_on_either_side() -> None:
    """The screen everyone actually wants, and the reason `compare` exists."""
    sql = _sql({"kind": "compare", "left": "close", "op": "gt", "right": "sma_200"})

    assert sql == "daily_bars.close > daily_indicators.sma_200"


def test_compare_rejects_a_unit_mismatch() -> None:
    """`close > volume` parses fine and means nothing."""
    with pytest.raises(ValidationError, match="units differ"):
        _tree({"kind": "compare", "left": "close", "op": "gt", "right": "volume"})


def test_compare_names_both_units_in_the_message() -> None:
    with pytest.raises(ValidationError) as caught:
        _tree({"kind": "compare", "left": "close", "op": "gt", "right": "volume_sma_20"})

    message = str(caught.value)
    assert "price" in message and "shares" in message


def test_compare_rejects_a_field_that_only_supports_numeric() -> None:
    """RSI is bounded 0-100 and shares that scale with nothing else here."""
    with pytest.raises(ValidationError, match="does not support compare"):
        _tree({"kind": "compare", "left": "rsi_14", "op": "gt", "right": "rsi_14"})


# --------------------------------------------------------------- known fields


def test_an_unknown_field_is_rejected_before_any_sql_exists() -> None:
    with pytest.raises(ValidationError, match="Unknown field"):
        _tree(_numeric(field="market_cap"))


def test_a_field_name_is_never_taken_from_the_request() -> None:
    """The defence, stated as a test: an injection attempt is just an unknown key."""
    with pytest.raises(ValidationError, match="Unknown field"):
        _tree(_numeric(field="close; DROP TABLE symbols --"))


def test_an_unknown_operator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _tree(_numeric(op="approximately"))


def test_a_display_only_field_cannot_be_filtered_on() -> None:
    """`ticker` is in the registry to be sorted and shown, not screened."""
    with pytest.raises(ValidationError, match="does not support"):
        _tree(_numeric(field="ticker"))


def test_a_category_field_cannot_take_a_numeric_filter() -> None:
    with pytest.raises(ValidationError, match="does not support numeric"):
        _tree(_numeric(field="exchange"))


def test_an_unknown_asset_type_is_rejected() -> None:
    """Its values are schema, unlike an exchange's, so a typo is worth a 422."""
    with pytest.raises(ValidationError, match="Unknown asset_type"):
        _tree({"kind": "category", "field": "asset_type", "op": "in", "values": ["crypto"]})


def test_an_unrecognised_exchange_is_allowed() -> None:
    """Exchanges are data, not schema: an unknown one is a screen matching nothing."""
    assert "'MOON'" in _sql(
        {"kind": "category", "field": "exchange", "op": "in", "values": ["MOON"]}
    )


# ------------------------------------------------------------------- decimals


def test_a_json_number_is_rejected_rather_than_coerced() -> None:
    """A float operand is the `100.09999999999999` bug this project bans."""
    with pytest.raises(ValidationError, match="as strings"):
        _tree({"kind": "numeric", "field": "close", "op": "gte", "value": 100.10})


def test_an_integer_json_number_is_rejected_too() -> None:
    """One rule, not two: quoting every value is easier to get right than a rule
    about which numbers happen to survive a round trip."""
    with pytest.raises(ValidationError, match="as strings"):
        _tree({"kind": "numeric", "field": "volume", "op": "gt", "value": 1_000_000})


def test_a_string_decimal_keeps_its_exact_value() -> None:
    assert _sql(_numeric(op="gte", value="100.10")) == "daily_bars.close >= 100.10"


def test_a_non_finite_value_is_rejected() -> None:
    """NaN in a comparison is a predicate nothing can satisfy and nobody meant."""
    with pytest.raises(ValidationError):
        _tree(_numeric(value="NaN"))


def test_an_absurdly_wide_value_is_rejected_here_not_by_postgres() -> None:
    with pytest.raises(ValidationError):
        _tree(_numeric(value="1" * 25))


# ------------------------------------------------------------------ precedence


def test_nesting_is_what_supplies_precedence() -> None:
    """`a AND (b OR c)` and `(a AND b) OR c` are different trees and must stay
    different queries. If the compiler flattened either one, both would render
    the same and the screener would silently answer a question nobody asked."""
    a, b, c = (
        _numeric("close", "gt", "1"),
        _numeric("close", "gt", "2"),
        _numeric("close", "gt", "3"),
    )

    and_over_or = _sql(_group("and", a, _group("or", b, c)))
    or_over_and = _sql(_group("or", _group("and", a, b), c))

    assert and_over_or == (
        "daily_bars.close > 1 AND (daily_bars.close > 2 OR daily_bars.close > 3)"
    )
    assert or_over_and == ("daily_bars.close > 1 AND daily_bars.close > 2 OR daily_bars.close > 3")
    assert and_over_or != or_over_and


def test_a_group_of_one_still_compiles() -> None:
    assert _sql(_group("and", _numeric())) == "daily_bars.close > 100"


def test_an_empty_group_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _tree(_group("and"))


# ----------------------------------------------------------------------- caps


def test_a_tree_at_the_depth_cap_is_accepted() -> None:
    assert _tree(_nest(MAX_DEPTH)) is not None


def test_a_tree_one_past_the_depth_cap_is_rejected() -> None:
    with pytest.raises(ValidationError, match="nested"):
        _tree(_nest(MAX_DEPTH + 1))


def test_a_tree_at_the_node_cap_is_accepted() -> None:
    leaves = [_numeric() for _ in range(MAX_NODES - 1)]
    assert _tree(_group("and", *leaves)) is not None


def test_a_tree_one_past_the_node_cap_is_rejected() -> None:
    leaves = [_numeric() for _ in range(MAX_NODES)]
    with pytest.raises(ValidationError, match="nodes"):
        _tree(_group("and", *leaves))


def test_the_node_cap_counts_the_whole_tree_not_one_group() -> None:
    """Two groups of fifteen is thirty-two nodes, and each group is under the
    per-list ceiling — so a cap enforced per group would let this through."""
    half = [_numeric() for _ in range(15)]
    with pytest.raises(ValidationError, match="nodes"):
        _tree(_group("and", _group("or", *half), _group("or", *half)))


def test_a_category_list_at_the_cap_is_accepted() -> None:
    values = [f"EX{i}" for i in range(MAX_CATEGORY_VALUES)]
    assert _tree({"kind": "category", "field": "exchange", "op": "in", "values": values})


def test_a_category_list_one_past_the_cap_is_rejected() -> None:
    values = [f"EX{i}" for i in range(MAX_CATEGORY_VALUES + 1)]
    with pytest.raises(ValidationError):
        _tree({"kind": "category", "field": "exchange", "op": "in", "values": values})


# ---------------------------------------------------------- referenced fields


def test_referenced_fields_returns_every_key_in_first_seen_order() -> None:
    """The results table shows the numbers that caused the match, so the run has
    to know which columns those are."""
    tree = _group(
        "and",
        {"kind": "compare", "left": "close", "op": "gt", "right": "sma_200"},
        _group("or", _numeric("rsi_14", "lt", "30"), _numeric("close", "gt", "5")),
    )

    assert referenced_fields(_tree(tree)) == ["close", "sma_200", "rsi_14"]  # type: ignore[arg-type]


# --------------------------------------------------------------- stored trees


def test_a_stored_tree_round_trips() -> None:
    tree = _tree(_group("and", _numeric()))
    assert parse_stored_filters(tree.model_dump(mode="json")) == tree  # type: ignore[attr-defined]


def test_a_stored_tree_naming_a_vanished_field_is_a_422_not_a_500() -> None:
    """The case a later phase creates by dropping an indicator column."""
    from app.core.exceptions import ValidationError as DomainValidationError

    stale = {"kind": "numeric", "field": "price_to_book", "op": "gt", "value": "2"}
    with pytest.raises(DomainValidationError) as caught:
        parse_stored_filters(stale)

    assert caught.value.status_code == 422
    assert "price_to_book" in caught.value.detail


def test_a_stored_tree_past_the_caps_is_rejected_on_the_way_out_too() -> None:
    """JSONB enforces nothing, so a row edited by hand must not bypass the caps."""
    from app.core.exceptions import ValidationError as DomainValidationError

    oversized = _nest(MAX_DEPTH + 1)
    with pytest.raises(DomainValidationError):
        parse_stored_filters(oversized)


# ------------------------------------------------------------------- registry


def test_every_registry_entry_declares_a_label_and_a_unit() -> None:
    """The catalogue is the UI's only source, so a blank label is a blank
    dropdown entry rather than a caught error."""
    for key, spec in FIELDS.items():
        assert spec.key == key
        assert spec.label
        assert spec.unit


def test_category_fields_are_the_only_ones_with_permitted_values() -> None:
    from app.core.enums import FilterKind

    for spec in FIELDS.values():
        if not spec.supports(FilterKind.CATEGORY):
            assert spec.category_values == (), spec.key
