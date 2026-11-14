"""The edge rules, the screen-alert set difference, and alert conditions' registries.

``decide`` is pure, so every case in the spec's list is a direct call here. The
integration suite checks the same rules end to end, through a job run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.enums import AlertKind, FilterKind, TriggerMode
from app.schemas.alert import AlertCreateRequest
from app.schemas.screener import ScreenerRunRequest
from app.services.alerts.evaluation import decide, describe_condition, new_entrants
from app.services.screener.fields import FIELDS, INTRADAY_FIELDS

NOW = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)


def step(
    last: bool | None,
    new: bool | None,
    *,
    mode: TriggerMode = TriggerMode.REARM,
    cooldown: int = 60,
    last_fired: datetime | None = None,
    now: datetime = NOW,
):
    return decide(
        last_state=last,
        new_state=new,
        trigger_mode=mode,
        cooldown_minutes=cooldown,
        last_triggered_at=last_fired,
        now=now,
    )


# ------------------------------------------------------------------------ edges


def test_false_to_true_fires() -> None:
    outcome = step(False, True)
    assert outcome.fire and outcome.last_state is True


def test_true_to_true_does_not_fire() -> None:
    outcome = step(True, True)
    assert not outcome.fire and outcome.last_state is True


def test_true_to_false_records_without_firing() -> None:
    outcome = step(True, False)
    assert not outcome.fire and outcome.last_state is False


def test_null_leaves_the_state_alone() -> None:
    """true -> null -> true is not a crossing: the null did not reset anything."""
    after_null = step(True, None)
    assert not after_null.fire and after_null.last_state is True

    after_true = step(after_null.last_state, True)
    assert not after_true.fire


def test_the_first_definite_evaluation_never_fires() -> None:
    """A new (or edited, or resumed) alert has no state; a TRUE only records."""
    outcome = step(None, True)
    assert not outcome.fire and outcome.last_state is True


def test_an_alert_that_has_only_seen_nulls_still_records_first() -> None:
    outcome = step(step(None, None).last_state, True)
    assert not outcome.fire


def test_an_edge_inside_the_cooldown_is_suppressed_but_tracked() -> None:
    outcome = step(False, True, cooldown=60, last_fired=NOW - timedelta(minutes=59))

    assert not outcome.fire
    assert outcome.suppressed
    assert outcome.last_state is True, "the state still moves, so the next edge is real"


def test_an_edge_one_minute_after_the_cooldown_fires() -> None:
    outcome = step(False, True, cooldown=60, last_fired=NOW - timedelta(minutes=61))
    assert outcome.fire


def test_an_edge_exactly_at_the_end_of_the_cooldown_fires() -> None:
    outcome = step(False, True, cooldown=60, last_fired=NOW - timedelta(minutes=60))
    assert outcome.fire


def test_once_ignores_the_cooldown() -> None:
    """A `once` alert is only evaluated while active, i.e. before it has fired."""
    outcome = step(False, True, mode=TriggerMode.ONCE, last_fired=NOW - timedelta(minutes=1))
    assert outcome.fire


# ----------------------------------------------------------------------- screen

A, B, C = (uuid.uuid7() for _ in range(3))


def test_a_screens_first_run_only_records() -> None:
    assert new_entrants(None, [A, B]) == []


def test_a_new_entrant_is_reported() -> None:
    assert new_entrants([A], [A, B]) == [B]


def test_leaving_is_not_an_entrant_and_re_entering_is() -> None:
    assert new_entrants([A, B], [A]) == []
    assert new_entrants([A], [A, B]) == [B]


def test_an_empty_previous_set_is_not_a_first_run() -> None:
    """[] means "nothing matched yesterday", which is different from "never ran"."""
    assert new_entrants([], [C]) == [C]


# -------------------------------------------------------------------- summaries


def test_a_condition_reads_as_a_sentence() -> None:
    tree = {
        "kind": "group",
        "op": "and",
        "children": [
            {"kind": "compare", "left": "price", "op": "gt", "right": "sma_200"},
            {"kind": "numeric", "field": "rsi_14", "op": "lt", "value": "30"},
        ],
    }
    assert describe_condition(tree) == "Price > SMA 200 AND RSI 14 < 30"


def test_a_stale_condition_still_gets_a_summary() -> None:
    """The list never parses trees, so a removed field must not break it."""
    tree = {"kind": "numeric", "field": "gone_field", "op": "gt", "value": "1"}
    assert describe_condition(tree) == "gone_field > 1"


def test_a_garbled_condition_does_not_raise() -> None:
    assert describe_condition({"kind": "numeric"}) == "(unreadable condition)"


# ------------------------------------------------------------------- registries


def _alert(kind: str, condition: dict) -> AlertCreateRequest:
    return AlertCreateRequest.model_validate(
        {"name": "x", "kind": kind, "ticker": "AAPL", "condition": condition}
    )


def test_an_intraday_alert_can_compare_a_live_price_with_last_nights_average() -> None:
    request = _alert(
        "intraday", {"kind": "compare", "left": "price", "op": "gt", "right": "sma_200"}
    )
    assert request.kind is AlertKind.INTRADAY


def test_an_intraday_alert_cannot_name_a_bar_field() -> None:
    """There is no bar until the close, so `close` means nothing intraday."""
    with pytest.raises(ValidationError, match="GET /alerts/fields"):
        _alert("intraday", {"kind": "numeric", "field": "close", "op": "gt", "value": "1"})


def test_an_end_of_day_alert_cannot_name_a_live_field() -> None:
    with pytest.raises(ValidationError, match="not available here"):
        _alert("end_of_day", {"kind": "numeric", "field": "price", "op": "gt", "value": "1"})


def test_a_screen_still_cannot_name_a_live_field() -> None:
    """Adding the intraday registry must not have widened what a screen accepts."""
    with pytest.raises(ValidationError, match="GET /screener/fields"):
        ScreenerRunRequest.model_validate(
            {"filters": {"kind": "numeric", "field": "price", "op": "gt", "value": "1"}}
        )


def test_a_screen_alert_takes_no_condition() -> None:
    with pytest.raises(ValidationError, match="follows its preset"):
        AlertCreateRequest.model_validate(
            {
                "name": "x",
                "kind": "screen",
                "preset_id": str(uuid.uuid7()),
                "condition": {"kind": "numeric", "field": "close", "op": "gt", "value": "1"},
            }
        )


def test_a_screen_alert_cannot_be_once() -> None:
    with pytest.raises(ValidationError, match="always 'rearm'"):
        AlertCreateRequest.model_validate(
            {"name": "x", "kind": "screen", "preset_id": str(uuid.uuid7()), "trigger_mode": "once"}
        )


def test_shared_keys_mean_the_same_thing_in_every_registry() -> None:
    """Node validation checks units and kinds against the union of registries.

    That is only sound if a key both registries define has the same unit and
    filter kinds in each.
    """
    for key in FIELDS.keys() & INTRADAY_FIELDS.keys():
        assert FIELDS[key].unit is INTRADAY_FIELDS[key].unit, key
        assert FIELDS[key].kinds == INTRADAY_FIELDS[key].kinds, key


def test_the_intraday_registry_has_no_bar_fields() -> None:
    assert not {"close", "open", "high", "low"} & INTRADAY_FIELDS.keys()
    assert INTRADAY_FIELDS["price"].supports(FilterKind.COMPARE)
