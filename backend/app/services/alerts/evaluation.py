"""When an alert fires: the edge rules, as pure functions.

No session and no clock. Each function takes the alert's stored state and the
new result and returns what to write. The rules live here, in one tested
place, rather than inside a job that would need a database to test.

**Firing is edge-triggered.** An alert fires when its condition goes from false
to true, not whenever it is true. A level check that fired every minute for as
long as AAPL stayed above 200 would be a notification storm, not an alert.

- ``last_state`` is the last *definite* result. A ``NULL`` evaluation (the
  ``sma_200`` it compares against has no value yet, say) leaves it unchanged,
  so the next ``TRUE`` is not mistaken for a new crossing. That is Phase 4's
  "nulls do not match" rule applied over time.
- **The first definite evaluation only records.** While ``last_state`` is still
  ``NULL`` (a new alert, an edited condition, a resumed alert), a ``TRUE``
  becomes the state without firing. An alert that fires the moment it is saved
  tells the user nothing they did not already know, and the create response
  says so through ``condition_met_now`` instead.
- **The cooldown suppresses firing, not tracking.** A price oscillating around
  a level makes a new edge every few minutes, so a ``rearm`` alert will not
  fire again within ``cooldown_minutes`` of its last firing. ``last_state``
  keeps updating during the cooldown, so the edge that ends it is a real one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.enums import TriggerMode
from app.services.screener.fields import KNOWN_FIELDS

__all__ = ["Outcome", "decide", "describe_condition", "new_entrants"]


@dataclass(frozen=True, slots=True)
class Outcome:
    #: Record a firing: a trigger row and a notification.
    fire: bool
    #: What ``last_state`` becomes.
    last_state: bool | None
    #: An edge happened, but inside the cooldown. Logged, never notified.
    suppressed: bool = False


def decide(
    *,
    last_state: bool | None,
    new_state: bool | None,
    trigger_mode: TriggerMode,
    cooldown_minutes: int,
    last_triggered_at: datetime | None,
    now: datetime,
) -> Outcome:
    """Apply the edge rules to one evaluation of one alert."""
    if new_state is None:
        return Outcome(fire=False, last_state=last_state)
    if last_state is None:
        return Outcome(fire=False, last_state=new_state)
    if not (new_state and not last_state):
        return Outcome(fire=False, last_state=new_state)

    in_cooldown = (
        trigger_mode is TriggerMode.REARM
        and last_triggered_at is not None
        and now - last_triggered_at < timedelta(minutes=cooldown_minutes)
    )
    if in_cooldown:
        return Outcome(fire=False, last_state=new_state, suppressed=True)
    return Outcome(fire=True, last_state=new_state)


def new_entrants(
    previous: Sequence[uuid.UUID] | None, matches: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """The edge for a screen alert: today's matches that were not in yesterday's.

    ``previous is None`` means this is the first run, which only records the
    set. A symbol that leaves and later re-enters is a new entrant again.
    """
    if previous is None:
        return []
    seen = set(previous)
    return [symbol_id for symbol_id in matches if symbol_id not in seen]


# ----------------------------------------------------------- condition summary

_OPERATORS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "neq": "!="}


def describe_condition(raw: Mapping[str, Any] | None) -> str | None:
    """A one-line reading of a stored tree, such as ``Price > SMA 200``.

    Works on the raw ``JSONB`` and never raises. The alert list deliberately
    does not re-validate trees (one broken alert must not take the page down),
    so a tree naming a removed field still gets a summary, with the raw key
    standing in for the label.
    """
    if raw is None:
        return None
    try:
        return _describe(raw, top=True)
    except (KeyError, TypeError, AttributeError):
        return "(unreadable condition)"


def _label(key: Any) -> str:
    spec = KNOWN_FIELDS.get(str(key))
    return spec.label if spec is not None else str(key)


def _describe(node: Mapping[str, Any], *, top: bool) -> str:
    kind = node.get("kind")
    if kind == "numeric":
        field, op = _label(node["field"]), node["op"]
        if op == "between":
            return f"{field} between {node['value']} and {node['value2']}"
        return f"{field} {_OPERATORS.get(op, op)} {node['value']}"
    if kind == "compare":
        op = _OPERATORS.get(node["op"], node["op"])
        return f"{_label(node['left'])} {op} {_label(node['right'])}"
    if kind == "category":
        verb = "in" if node["op"] == "in" else "not in"
        return f"{_label(node['field'])} {verb} {', '.join(map(str, node['values']))}"
    if kind == "group":
        joiner = f" {str(node['op']).upper()} "
        text = joiner.join(_describe(child, top=False) for child in node["children"])
        return text if top else f"({text})"
    raise KeyError(kind)
