"""The filter tree, compiled to a SQLAlchemy boolean expression.

A pure function: no session, no I/O, no request. It takes a tree that Pydantic
has already validated and returns something that can go in a ``WHERE``. That
purity is the reason it is testable directly, which is the reason the phase
built it before any route existed.

**Nothing here reads a string from the request.** A node names a field by key;
the key was checked against the registry during validation and is resolved to a
``ColumnElement`` here. Operators are enum members mapped to Python's own
comparison functions. Literal values are bound parameters, as they are anywhere
in SQLAlchemy. There is no string interpolation on any path.

**Nulls do not match, and that is correct.** Every indicator column is nullable
and SQL three-valued logic means ``close > sma_200`` is ``NULL`` — not ``TRUE``
— when ``sma_200`` has no value, so a symbol with fewer than 200 bars silently
drops out of that screen. Nothing here "fixes" that with ``COALESCE``, which
would substitute a fabricated number and produce matches that are not true. It
is made *visible* instead: the response reports ``universe_size`` beside
``total_matched``.

The same applies to ``neq`` and to ``NOT IN``, which people expect to be the
complement of their positive form and which are not: ``NULL != 5`` is ``NULL``,
so a null-valued row fails both ``eq`` and ``neq``. That is SQL, it is the same
answer every database gives, and pretending otherwise would require exactly the
``COALESCE`` this file refuses.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, and_, or_

from app.core.enums import (
    CategoryOperator,
    GroupOperator,
    NumericOperator,
)
from app.schemas.screener import (
    CategoryFilter,
    CompareFilter,
    FilterNode,
    GroupFilter,
    NumericFilter,
)
from app.services.screener.fields import resolve

__all__ = ["compile_filters", "referenced_fields"]

_Comparison = Callable[[Any, Any], ColumnElement[bool]]

#: Python's own operators, which SQLAlchemy columns overload into SQL. Mapping
#: the enum to these rather than to strings is what keeps an operator from ever
#: being a piece of text near a query.
_COMPARISONS: dict[str, _Comparison] = {
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
    "eq": operator.eq,
    "neq": operator.ne,
}


def _numeric(node: NumericFilter) -> ColumnElement[bool]:
    column = resolve(node.field).expression
    if node.op is NumericOperator.BETWEEN:
        if node.value2 is None:
            # Unreachable: the schema rejects a 'between' without an upper
            # bound. Raising beats an assert, which -O would strip out.
            raise ValueError("A 'between' filter needs both 'value' and 'value2'.")
        return column.between(node.value, node.value2)
    return _COMPARISONS[node.op.value](column, node.value)


def _compare(node: CompareFilter) -> ColumnElement[bool]:
    left = resolve(node.left).expression
    right = resolve(node.right).expression
    return _COMPARISONS[node.op.value](left, right)


def _category(node: CategoryFilter) -> ColumnElement[bool]:
    column = resolve(node.field).expression
    if node.op is CategoryOperator.IN:
        return column.in_(node.values)
    return column.not_in(node.values)


def _group(node: GroupFilter) -> ColumnElement[bool]:
    children = [compile_filters(child) for child in node.children]
    combine = and_ if node.op is GroupOperator.AND else or_
    # SQLAlchemy parenthesises each nested boolean, so precedence comes from the
    # tree's shape and never from operator precedence in the rendered SQL:
    # `a AND (b OR c)` and `(a AND b) OR c` are different trees and stay
    # different queries.
    return combine(*children)


def compile_filters(node: FilterNode) -> ColumnElement[bool]:
    """Compile one validated node — and its subtree — into a SQL predicate."""
    match node:
        case NumericFilter():
            return _numeric(node)
        case CompareFilter():
            return _compare(node)
        case CategoryFilter():
            return _category(node)
        case GroupFilter():
            return _group(node)


def referenced_fields(node: FilterNode) -> list[str]:
    """Every field key the tree names, in first-seen order.

    The results table shows the numbers that caused a match, so the run has to
    know which columns those are. Order is preserved rather than sorted so the
    table's columns follow the order the user built the screen in.
    """
    seen: dict[str, None] = {}

    def walk(current: FilterNode) -> None:
        match current:
            case NumericFilter() | CategoryFilter():
                seen.setdefault(current.field, None)
            case CompareFilter():
                seen.setdefault(current.left, None)
                seen.setdefault(current.right, None)
            case GroupFilter():
                for child in current.children:
                    walk(child)

    walk(node)
    return list(seen)
