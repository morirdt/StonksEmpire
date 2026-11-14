"""API representation of a user's chart preferences.

The database stores the indicator lists as ``JSONB`` and therefore enforces
nothing about their contents. This module is what does — every key is validated
against a ``StrEnum``, so an unknown indicator is a 422 rather than a row that
renders as a missing line six months from now.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import ChartRange, Oscillator, PriceOverlay
from app.models.chart_preferences import UserChartPreferences

#: The price pane accepts at most this many overlays at once. Beyond three,
#: lines over candles stop being readable whatever the palette — and the
#: validated overlay palette only has three slots for the same reason. Enforced
#: here as well as in the toggle UI, so a hand-rolled client cannot store a
#: preference the chart is unable to draw.
MAX_ACTIVE_OVERLAYS = 3


def _deduplicate[T: str](values: list[T]) -> list[T]:
    """First occurrence wins, order preserved.

    Duplicates are a client bug rather than a user intent, and storing them
    would draw the same line twice.
    """
    seen: set[T] = set()
    unique: list[T] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


class ChartPreferencesRequest(BaseModel):
    """A full replacement, not a patch — every field is required.

    A PATCH shape would need a way to say "no overlays at all" that is distinct
    from "leave them alone", and `null` versus `[]` is exactly the distinction
    clients get wrong.
    """

    default_range: ChartRange
    active_overlays: Annotated[list[PriceOverlay], Field(max_length=MAX_ACTIVE_OVERLAYS)] = Field(
        default_factory=list
    )
    active_oscillators: list[Oscillator] = Field(default_factory=list)

    @field_validator("active_overlays", mode="after")
    @classmethod
    def _unique_overlays(cls, values: list[PriceOverlay]) -> list[PriceOverlay]:
        return _deduplicate(values)

    @field_validator("active_oscillators", mode="after")
    @classmethod
    def _unique_oscillators(cls, values: list[Oscillator]) -> list[Oscillator]:
        return _deduplicate(values)


class ChartPreferencesResponse(BaseModel):
    """What the controls are seeded from.

    Returned with defaults filled in when the user has never saved anything, so
    a client never has to special-case "no preferences yet".
    """

    model_config = ConfigDict(from_attributes=True)

    default_range: ChartRange
    active_overlays: list[PriceOverlay]
    active_oscillators: list[Oscillator]

    @classmethod
    def from_row(cls, row: UserChartPreferences) -> ChartPreferencesResponse:
        return cls(
            default_range=ChartRange(row.default_range),
            active_overlays=[PriceOverlay(k) for k in row.active_overlays],
            active_oscillators=[Oscillator(k) for k in row.active_oscillators],
        )
