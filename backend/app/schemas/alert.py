"""Alerts on the wire.

A condition is a Phase 4 filter tree, validated by the same node models. Which
fields it may name depends on ``kind``: an intraday alert uses the intraday
registry (``price``, ``day_high``, …) and an end-of-day alert uses the
screener's (``close``, ``high``, …). The nodes validate structure against every
known field, and this module then checks the tree against the kind's own
registry. So ``close`` in an intraday alert is a 422 naming
``GET /alerts/fields``.

Like presets, the list does not re-parse stored trees, but opening or editing an
alert does. One broken alert must not take down the page that lists them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.enums import AlertKind, AlertStatus, TriggerMode
from app.schemas.screener import FilterNode, enforce_caps, require_registry
from app.services.screener.fields import FIELDS, INTRADAY_FIELDS, FieldRegistry

__all__ = [
    "AlertCreateRequest",
    "AlertCreateResponse",
    "AlertResponse",
    "AlertSummary",
    "AlertTriggerPage",
    "AlertTriggerResponse",
    "AlertUpdateRequest",
    "registry_for",
]

ALERT_FIELDS_CATALOGUE = "GET /alerts/fields"


def registry_for(kind: AlertKind) -> FieldRegistry:
    """The field registry an alert of ``kind`` is evaluated against."""
    return INTRADAY_FIELDS if kind is AlertKind.INTRADAY else FIELDS


def check_condition(tree: Any, kind: AlertKind) -> Any:
    """Raise ``ValueError`` unless ``tree`` uses only ``kind``'s registry."""
    return require_registry(tree, registry_for(kind), catalogue=ALERT_FIELDS_CATALOGUE)


Name = Field(min_length=1, max_length=80)
Cooldown = Field(default=None, ge=5, le=1440)


class AlertCreateRequest(BaseModel):
    """Create an alert.

    ``intraday`` and ``end_of_day`` need ``ticker`` and ``condition``. ``screen``
    needs ``preset_id`` and nothing else, because its condition is the preset's.
    ``trigger_mode`` defaults to ``once``, except for screen alerts, which are
    always ``rearm``: they run once a day and fire on new entrants.
    """

    name: str = Name
    kind: AlertKind
    ticker: str | None = Field(default=None, min_length=1, max_length=16)
    preset_id: uuid.UUID | None = None
    condition: FilterNode | None = None
    trigger_mode: TriggerMode | None = None
    cooldown_minutes: int | None = Cooldown

    @field_validator("condition", mode="after")
    @classmethod
    def _within_caps(cls, node: Any) -> Any:
        return None if node is None else enforce_caps(node)

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> Self:
        if self.kind is AlertKind.SCREEN:
            if self.preset_id is None:
                raise ValueError("A screen alert needs 'preset_id'.")
            if self.ticker is not None or self.condition is not None:
                raise ValueError(
                    "A screen alert takes no 'ticker' or 'condition': it follows its preset."
                )
            if self.trigger_mode is TriggerMode.ONCE:
                raise ValueError("A screen alert is always 'rearm': it fires on new entrants.")
            return self

        if self.ticker is None or self.condition is None:
            raise ValueError(f"An {self.kind.value} alert needs 'ticker' and 'condition'.")
        if self.preset_id is not None:
            raise ValueError("Only a screen alert takes 'preset_id'.")
        check_condition(self.condition, self.kind)
        return self


class AlertUpdateRequest(BaseModel):
    """A patch. An omitted field is left alone.

    Editing the condition resets the alert's state, so the next evaluation
    only records and cannot fire on the edit itself. ``status`` may be set to
    ``active`` or ``paused``. Setting ``active`` on a triggered or disabled
    alert re-arms it, which is the explicit action ``once`` requires.
    """

    name: str | None = Field(default=None, min_length=1, max_length=80)
    condition: FilterNode | None = None
    trigger_mode: TriggerMode | None = None
    cooldown_minutes: int | None = Cooldown
    status: Literal[AlertStatus.ACTIVE, AlertStatus.PAUSED] | None = None

    @field_validator("condition", mode="after")
    @classmethod
    def _within_caps(cls, node: Any) -> Any:
        return None if node is None else enforce_caps(node)


class AlertSummary(BaseModel):
    """What the list shows. Deliberately **without** the parsed condition.

    ``condition_summary`` is a one-line reading of the stored tree that never
    fails, so a stale alert still lists, and still says what it was watching.
    """

    id: uuid.UUID
    name: str
    kind: AlertKind
    ticker: str | None
    preset_id: uuid.UUID | None
    preset_name: str | None
    condition_summary: str | None
    trigger_mode: TriggerMode
    cooldown_minutes: int
    status: AlertStatus
    disabled_reason: str | None
    last_state: bool | None
    #: What tells a user the worker is alive: "last checked 3 days ago" is a
    #: problem they can see, where "not triggered" alone tells them nothing.
    last_evaluated_at: datetime | None
    last_triggered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertResponse(AlertSummary):
    condition: FilterNode | None


class AlertCreateResponse(AlertResponse):
    #: Whether the condition already holds. A new alert does not fire on its
    #: first evaluation, so this is how the user hears that it is already true.
    #: ``null`` when there is no data to tell (no quote or bar yet), and for
    #: screen alerts.
    condition_met_now: bool | None


class AlertTriggerResponse(BaseModel):
    id: uuid.UUID
    triggered_at: datetime
    #: The bar date, for end-of-day and screen alerts.
    as_of_date: date | None
    #: The quote's fetch time, for intraday alerts.
    as_of_time: datetime | None
    #: The values that made it fire. For a symbol alert: ``ticker``,
    #: ``condition``, and ``values``, a map of field key to decimal string. For
    #: a screen alert: ``preset``, ``entrants``, ``matched``, and ``as_of``.
    snapshot: dict[str, Any]


class AlertTriggerPage(BaseModel):
    items: list[AlertTriggerResponse]
    next_cursor: str | None
