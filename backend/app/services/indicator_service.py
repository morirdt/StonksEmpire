"""Technical indicators, computed from a bar series.

Pure functions over a list of bars: no I/O, no session, no provider. That is
deliberate — it means the maths unit-tests against hand-checked series without
a database, which is the only practical way to be confident in it.

**Indicators are computed here, never fetched.** Providers disagree with each
other on RSI smoothing and on what "MACD" means, so a fetched indicator would
make the Phase 3 chart and the Phase 4 screen capable of contradicting each
other about the same symbol on the same day. Computing locally costs a few
hundred Decimal operations and removes the whole class of problem.

Everything is ``Decimal``. No pandas and no numpy: both would force prices
through ``float``, which ``CLAUDE.md`` forbids for exactly the reason you would
expect — a cent lost per operation is a cent lost in Phase 6's P&L.

Conventions used here, chosen because they are what charting packages mean by
these names:

* EMA seeds from the simple average of the first ``period`` values, rather than
  from the first value alone. The alternative converges to the same place but
  disagrees for the first hundred bars, which is exactly the window a new
  backfill shows you.
* RSI and ATR use **Wilder's** smoothing (``1/n``), not a ``2/(n+1)`` EMA.
* MACD signal is a 9-period EMA *of the MACD line*, so it starts 8 bars after
  the MACD line itself does — not at the same index.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.integrations.market_data.base import Bar

__all__ = ["IndicatorSet", "compute_indicators"]

#: NUMERIC(18, 6) is the storage type, so results are quantized to match. Doing
#: it once at the end keeps intermediate precision full.
_QUANT = Decimal("0.000001")

_SMA_PERIODS = (20, 50, 200)
_EMA_FAST, _EMA_SLOW, _MACD_SIGNAL = 12, 26, 9
_RSI_PERIOD = 14
_ATR_PERIOD = 14
_VOLUME_SMA_PERIOD = 20


@dataclass(frozen=True, slots=True)
class IndicatorSet:
    """One row of ``daily_indicators``. Every field is optional.

    The leading bars of any window genuinely have no value — a 200-day average
    needs 200 days — and ``None`` says that honestly where a zero would lie.
    """

    trade_date: date
    sma_20: Decimal | None = None
    sma_50: Decimal | None = None
    sma_200: Decimal | None = None
    ema_12: Decimal | None = None
    ema_26: Decimal | None = None
    rsi_14: Decimal | None = None
    macd: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_histogram: Decimal | None = None
    atr_14: Decimal | None = None
    volume_sma_20: Decimal | None = None


def _quantize(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_QUANT)


def _sma(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Simple moving average, aligned to ``values`` with a leading ``None`` run.

    Uses a running total rather than re-summing each window: over 200-period
    averages on a few thousand bars the difference is noticeable, and with
    Decimal there is no floating-point drift to worry about from doing so.
    """
    out: list[Decimal | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out

    window = sum(values[:period], Decimal(0))
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def _ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Exponential moving average, seeded from the SMA of the first window."""
    out: list[Decimal | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out

    multiplier = Decimal(2) / (period + 1)
    previous = sum(values[:period], Decimal(0)) / period
    out[period - 1] = previous
    for i in range(period, len(values)):
        previous = (values[i] - previous) * multiplier + previous
        out[i] = previous
    return out


def _wilder(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Wilder's smoothing: seed with a simple average, then ``(prev*(n-1)+x)/n``.

    This is what RSI and ATR mean by "average", and it is *not* the same as an
    EMA of the same period — Wilder's is equivalent to an EMA of ``2n-1``.
    Using the wrong one is the single most common way two charts disagree.
    """
    out: list[Decimal | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out

    previous = sum(values[:period], Decimal(0)) / period
    out[period - 1] = previous
    for i in range(period, len(values)):
        previous = (previous * (period - 1) + values[i]) / period
        out[i] = previous
    return out


def _rsi(closes: Sequence[Decimal], period: int) -> list[Decimal | None]:
    out: list[Decimal | None] = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains = [max(closes[i] - closes[i - 1], Decimal(0)) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], Decimal(0)) for i in range(1, len(closes))]

    avg_gain = _wilder(gains, period)
    avg_loss = _wilder(losses, period)

    for i in range(len(gains)):
        gain, loss = avg_gain[i], avg_loss[i]
        if gain is None or loss is None:
            continue
        if loss == 0:
            # No downside in the window at all. RS is undefined rather than
            # infinite, and every charting package renders this as 100.
            out[i + 1] = Decimal(100)
        elif gain == 0:
            out[i + 1] = Decimal(0)
        else:
            rs = gain / loss
            out[i + 1] = Decimal(100) - (Decimal(100) / (Decimal(1) + rs))
    return out


def _true_ranges(bars: Sequence[Bar]) -> list[Decimal]:
    """True range per bar, starting at the second — the first has no prior close."""
    ranges: list[Decimal] = []
    for i in range(1, len(bars)):
        previous_close = bars[i - 1].close
        bar = bars[i]
        ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return ranges


def compute_indicators(bars: Sequence[Bar]) -> list[IndicatorSet]:
    """One ``IndicatorSet`` per bar, in the same order.

    ``bars`` must be oldest-first and gap-free in the sense that every row is a
    real trading day; the maths is positional, so handing it an unsorted series
    silently produces nonsense rather than an error. The repository is what
    guarantees the ordering.
    """
    if not bars:
        return []

    closes = [b.close for b in bars]
    volumes = [Decimal(b.volume) for b in bars]

    smas = {period: _sma(closes, period) for period in _SMA_PERIODS}
    ema_fast = _ema(closes, _EMA_FAST)
    ema_slow = _ema(closes, _EMA_SLOW)
    rsi = _rsi(closes, _RSI_PERIOD)
    volume_sma = _sma(volumes, _VOLUME_SMA_PERIOD)

    # MACD exists only where both EMAs do, which is where the slow one starts.
    macd: list[Decimal | None] = [
        fast - slow if fast is not None and slow is not None else None
        for fast, slow in zip(ema_fast, ema_slow, strict=True)
    ]

    # The signal line is an EMA *of the MACD line*, so it is computed over the
    # dense subsequence and then mapped back to the original positions. Running
    # it over the padded list would treat the leading Nones as data.
    dense_indices = [i for i, value in enumerate(macd) if value is not None]
    dense_macd = [value for value in macd if value is not None]
    signal: list[Decimal | None] = [None] * len(bars)
    for position, value in enumerate(_ema(dense_macd, _MACD_SIGNAL)):
        signal[dense_indices[position]] = value

    # ATR is indexed off true ranges, which start one bar late.
    atr: list[Decimal | None] = [None] * len(bars)
    for position, value in enumerate(_wilder(_true_ranges(bars), _ATR_PERIOD)):
        atr[position + 1] = value

    rows: list[IndicatorSet] = []
    for i, bar in enumerate(bars):
        macd_value, signal_value = macd[i], signal[i]
        histogram = (
            macd_value - signal_value
            if macd_value is not None and signal_value is not None
            else None
        )
        rows.append(
            IndicatorSet(
                trade_date=bar.trade_date,
                sma_20=_quantize(smas[20][i]),
                sma_50=_quantize(smas[50][i]),
                sma_200=_quantize(smas[200][i]),
                ema_12=_quantize(ema_fast[i]),
                ema_26=_quantize(ema_slow[i]),
                rsi_14=_quantize(rsi[i]),
                macd=_quantize(macd_value),
                macd_signal=_quantize(signal_value),
                macd_histogram=_quantize(histogram),
                atr_14=_quantize(atr[i]),
                volume_sma_20=_quantize(volume_sma[i]),
            )
        )
    return rows
