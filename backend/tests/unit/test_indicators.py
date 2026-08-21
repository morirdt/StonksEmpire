"""Indicator maths, against series whose answers are known by hand.

Two kinds of assertion here. The first is arithmetic: on a series simple enough
to compute in your head, the output must match exactly. The second is
alignment: each indicator must start at precisely the bar where it becomes
defined, because an off-by-one in a leading window is invisible on a chart and
catastrophic in a Phase 4 screen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.integrations.market_data.base import Bar
from app.services.indicator_service import compute_indicators

_START = date(2024, 1, 1)


def _bars(
    closes: Sequence[float | int | str],
    *,
    highs: Sequence[float | int | str] | None = None,
    lows: Sequence[float | int | str] | None = None,
    volumes: Sequence[int] | None = None,
) -> list[Bar]:
    """Build a bar series from closes, with sane defaults for the rest."""
    result = []
    for i, close in enumerate(closes):
        c = Decimal(str(close))
        result.append(
            Bar(
                trade_date=_START + timedelta(days=i),
                open=c,
                high=Decimal(str(highs[i])) if highs else c,
                low=Decimal(str(lows[i])) if lows else c,
                close=c,
                volume=volumes[i] if volumes else 1_000,
            )
        )
    return result


#: Results are stored as NUMERIC(18, 6), so any two of them can disagree by one
#: unit in the last place purely from rounding at that boundary. Derived values
#: are computed at full precision and quantized once, which is the more accurate
#: choice but means `quantize(a) - quantize(b)` is not always `quantize(a - b)`.
_ULP = Decimal("0.000001")


def _values(row: object) -> list[Decimal | None]:
    return [getattr(row, f.name) for f in fields(row) if f.name != "trade_date"]  # type: ignore[arg-type]


def _first_defined(rows: list, field: str) -> int | None:
    for i, row in enumerate(rows):
        if getattr(row, field) is not None:
            return i
    return None


# --------------------------------------------------------------------- basics


def test_an_empty_series_produces_nothing() -> None:
    assert compute_indicators([]) == []


def test_one_row_per_bar_in_the_same_order() -> None:
    bars = _bars(range(1, 31))

    rows = compute_indicators(bars)

    assert len(rows) == len(bars)
    assert [r.trade_date for r in rows] == [b.trade_date for b in bars]


def test_every_value_is_a_decimal() -> None:
    """Never a float. A cent lost per operation is a cent lost in Phase 6 P&L."""
    rows = compute_indicators(_bars(range(1, 61)))

    values = [v for row in rows for v in _values(row)]
    assert values, "the series should have produced something"
    assert all(v is None or isinstance(v, Decimal) for v in values)


# ------------------------------------------------------------------------ SMA


def test_sma_20_matches_a_hand_computed_average() -> None:
    rows = compute_indicators(_bars(range(1, 26)))

    # Closes are 1..25, so the first full window averages 1..20.
    assert rows[19].sma_20 == Decimal("10.5")
    assert rows[20].sma_20 == Decimal("11.5")
    assert rows[24].sma_20 == Decimal("15.5")


def test_sma_is_none_until_its_window_is_full() -> None:
    rows = compute_indicators(_bars(range(1, 26)))

    assert all(r.sma_20 is None for r in rows[:19])
    assert rows[19].sma_20 is not None


def test_a_flat_series_averages_to_itself() -> None:
    rows = compute_indicators(_bars([50] * 60))

    assert rows[-1].sma_20 == Decimal("50")
    assert rows[-1].sma_50 == Decimal("50")
    assert rows[-1].ema_12 == Decimal("50")
    assert rows[-1].ema_26 == Decimal("50")


def test_volume_sma_averages_volume_not_price() -> None:
    rows = compute_indicators(_bars([10] * 25, volumes=[100] * 20 + [200] * 5))

    assert rows[19].volume_sma_20 == Decimal("100")
    # Five 200s have displaced five 100s: (15*100 + 5*200) / 20.
    assert rows[24].volume_sma_20 == Decimal("125")


# ------------------------------------------------------------------------ EMA


def test_ema_seeds_from_the_simple_average_of_its_first_window() -> None:
    rows = compute_indicators(_bars(range(1, 41)))

    assert rows[11].ema_12 == Decimal("6.5")  # mean of 1..12
    assert rows[25].ema_26 == Decimal("13.5")  # mean of 1..26


def test_ema_then_tracks_the_series() -> None:
    rows = compute_indicators(_bars(range(1, 41)))

    # k = 2/13; previous 6.5, next close 13 => 6.5 + (13 - 6.5) * 2/13 = 7.5
    assert rows[12].ema_12 == Decimal("7.5")


# ------------------------------------------------------------------------ RSI


def test_rsi_is_100_when_nothing_falls() -> None:
    """No downside in the window: RS is undefined, and every chart draws 100."""
    rows = compute_indicators(_bars(range(1, 31)))

    assert rows[14].rsi_14 == Decimal("100")


def test_rsi_is_0_when_nothing_rises() -> None:
    rows = compute_indicators(_bars(range(30, 0, -1)))

    assert rows[14].rsi_14 == Decimal("0")


def test_rsi_is_50_when_gains_and_losses_balance() -> None:
    # Alternating 100/101: over the first 14 changes, seven +1s and seven -1s,
    # so avg gain equals avg loss, RS is 1, and RSI is 50.
    closes = [100 if i % 2 == 0 else 101 for i in range(30)]

    rows = compute_indicators(_bars(closes))

    assert rows[14].rsi_14 == Decimal("50")


def test_rsi_stays_within_its_bounds() -> None:
    closes = [100 + (i * 7 % 13) - 6 for i in range(120)]

    rows = compute_indicators(_bars(closes))

    values = [r.rsi_14 for r in rows if r.rsi_14 is not None]
    assert values
    assert all(Decimal(0) <= v <= Decimal(100) for v in values)


# ------------------------------------------------------------------------ ATR


def test_atr_of_a_constant_range_is_that_range() -> None:
    # Every bar spans 2.00 and never gaps, so every true range is 2.00.
    closes = [100] * 40
    rows = compute_indicators(_bars(closes, highs=[101] * 40, lows=[99] * 40))

    assert rows[14].atr_14 == Decimal("2")
    assert rows[-1].atr_14 == Decimal("2")


def test_atr_accounts_for_gaps_between_sessions() -> None:
    """True range includes the prior close, or an overnight gap reads as calm."""
    closes = [100] * 20 + [120] * 20
    flat = compute_indicators(_bars([100] * 40, highs=[100.5] * 40, lows=[99.5] * 40))
    gapped = compute_indicators(
        _bars(closes, highs=[c + 0.5 for c in closes], lows=[c - 0.5 for c in closes])
    )

    assert gapped[-1].atr_14 is not None and flat[-1].atr_14 is not None
    assert gapped[-1].atr_14 > flat[-1].atr_14


# ----------------------------------------------------------------------- MACD


def test_macd_is_the_difference_of_the_two_emas() -> None:
    rows = compute_indicators(_bars([100 + (i % 17) for i in range(120)]))

    for row in rows:
        if row.ema_12 is not None and row.ema_26 is not None:
            assert row.macd is not None
            assert abs(row.macd - (row.ema_12 - row.ema_26)) <= _ULP
        else:
            assert row.macd is None


def test_the_signal_line_lags_the_macd_line_by_its_own_window() -> None:
    """A 9-period EMA *of the MACD line* starts 8 bars after the MACD line does.

    Computing it over the padded series instead would treat the leading Nones
    as data and start it in the wrong place — the kind of error that looks
    plausible on a chart.
    """
    rows = compute_indicators(_bars([100 + (i % 17) for i in range(120)]))

    macd_starts = _first_defined(rows, "macd")
    signal_starts = _first_defined(rows, "macd_signal")

    assert macd_starts == 25
    assert signal_starts == macd_starts + 8


def test_the_histogram_is_the_gap_between_them() -> None:
    rows = compute_indicators(_bars([100 + (i % 17) for i in range(120)]))

    for row in rows:
        if row.macd is not None and row.macd_signal is not None:
            assert row.macd_histogram is not None
            assert abs(row.macd_histogram - (row.macd - row.macd_signal)) <= _ULP
        else:
            assert row.macd_histogram is None


# ------------------------------------------------------------------ alignment


@pytest.mark.parametrize(
    ("field", "expected_index"),
    [
        ("sma_20", 19),
        ("sma_50", 49),
        ("sma_200", 199),
        ("ema_12", 11),
        ("ema_26", 25),
        ("rsi_14", 14),
        ("macd", 25),
        ("macd_signal", 33),
        ("macd_histogram", 33),
        ("atr_14", 14),
        ("volume_sma_20", 19),
    ],
)
def test_each_indicator_starts_exactly_where_it_becomes_defined(
    field: str,
    expected_index: int,
) -> None:
    rows = compute_indicators(_bars([100 + (i % 23) for i in range(260)]))

    assert _first_defined(rows, field) == expected_index


def test_a_short_series_yields_no_sma_200() -> None:
    """The 2-year backfill exists so this window is populated; 100 bars is not enough."""
    rows = compute_indicators(_bars([100 + (i % 7) for i in range(100)]))

    assert all(r.sma_200 is None for r in rows)
    assert any(r.sma_50 is not None for r in rows)


def test_a_series_shorter_than_every_window_is_all_none() -> None:
    rows = compute_indicators(_bars([100, 101, 102]))

    assert len(rows) == 3
    for row in rows:
        assert all(v is None for v in _values(row))
