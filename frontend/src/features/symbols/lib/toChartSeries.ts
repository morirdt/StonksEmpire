/**
 * API rows in, chart series out.
 *
 * Pure, and separate from the chart component on purpose. `CandlestickChart`
 * renders to a canvas, which Testing Library cannot see, so this function is
 * where the coverage that would otherwise be lost gets paid back — it is
 * everything the chart does that is not drawing.
 *
 * This is also the sanctioned place a price becomes a number. Prices arrive as
 * strings because the backend serialises `Decimal` that way to stop JavaScript
 * rounding them, and `lib/format.ts` owns the parse; the chart is a display
 * boundary, so it parses here and the numbers go straight into series data
 * rather than being passed around.
 */
import { toNumber } from '@/lib/format';
import type {
  Candle,
  ChartLine,
  ChartSeriesData,
  HistogramPoint,
  LinePoint,
  MarkTone,
  OscillatorPane,
} from '@/components/chart/types';
import type { Bar, Indicator } from '../api/useSymbolChart';
import {
  MAX_ACTIVE_OVERLAYS,
  OSCILLATOR_LABEL,
  OVERLAY_LABEL,
  OVERLAY_SLOT,
  RSI_REFERENCE_LEVELS,
  RSI_SCALE,
  type OscillatorKey,
  type OverlayKey,
} from './indicators';

export interface ActiveIndicators {
  overlays: OverlayKey[];
  oscillators: OscillatorKey[];
}

/** The three validated overlay colours, in slot order. From the chart theme. */
export type SlotColors = readonly [string, string, string];

type IndicatorField = Exclude<keyof Indicator, 'trade_date'>;

/**
 * One point per bar, whatever the indicator row says.
 *
 * The absent-`value` form is load-bearing: the library reads it as whitespace
 * and gaps the line. A zero would draw down to the bottom of the pane and look
 * like a crash, which is exactly what the leading 199 bars of a 200-day average
 * would produce.
 */
function lineFor(bars: Bar[], byDate: Map<string, Indicator>, field: IndicatorField): LinePoint[] {
  return bars.map((bar) => {
    const value = toNumber(byDate.get(bar.trade_date)?.[field] ?? null);
    return value === null ? { time: bar.trade_date } : { time: bar.trade_date, value };
  });
}

/** Above zero, below zero, or neither — position is the secondary encoding. */
function toneOf(value: number): MarkTone {
  if (value > 0) return 'profit';
  if (value < 0) return 'loss';
  return 'neutral';
}

function macdHistogram(bars: Bar[], byDate: Map<string, Indicator>): HistogramPoint[] {
  return bars.map((bar) => {
    const value = toNumber(byDate.get(bar.trade_date)?.macd_histogram ?? null);
    return value === null
      ? { time: bar.trade_date }
      : { time: bar.trade_date, value, tone: toneOf(value) };
  });
}

function oscillatorPane(
  key: OscillatorKey,
  bars: Bar[],
  byDate: Map<string, Indicator>,
  slots: SlotColors,
): OscillatorPane {
  if (key === 'rsi_14') {
    return {
      id: 'rsi_14',
      label: OSCILLATOR_LABEL.rsi_14,
      // One line, so no legend box — the pane title names it.
      lines: [
        {
          id: 'rsi_14',
          label: OSCILLATOR_LABEL.rsi_14,
          color: slots[0],
          data: lineFor(bars, byDate, 'rsi_14'),
        },
      ],
      fixedScale: RSI_SCALE,
      referenceLines: RSI_REFERENCE_LEVELS,
    };
  }

  return {
    id: 'macd',
    label: OSCILLATOR_LABEL.macd,
    // Two lines share this pane, so they are a validated pair and the pane
    // carries a legend. Slot order is fixed: MACD is blue, signal is amber.
    lines: [
      { id: 'macd', label: 'MACD', color: slots[0], data: lineFor(bars, byDate, 'macd') },
      {
        id: 'macd_signal',
        label: 'Signal',
        color: slots[1],
        data: lineFor(bars, byDate, 'macd_signal'),
      },
    ],
    histogram: { id: 'macd_histogram', data: macdHistogram(bars, byDate) },
    referenceLines: [0],
  };
}

export function toChartSeries(
  bars: Bar[],
  indicators: Indicator[],
  active: ActiveIndicators,
  slots: SlotColors,
): ChartSeriesData {
  if (bars.length === 0) {
    return { candles: [], volume: [], overlays: [], oscillators: [] };
  }

  // Joined on the date rather than by position. The backend guarantees the two
  // windows match, and asserts it in a test — but joining anyway means that if
  // they ever do not, the chart gaps instead of drawing every indicator against
  // the wrong candle, which is a bug nothing would surface.
  const byDate = new Map(indicators.map((row) => [row.trade_date, row]));

  const candles: Candle[] = bars.map((bar) => ({
    time: bar.trade_date,
    open: toNumber(bar.open) ?? 0,
    high: toNumber(bar.high) ?? 0,
    low: toNumber(bar.low) ?? 0,
    close: toNumber(bar.close) ?? 0,
  }));

  // One neutral tone. Volume's job is magnitude, and colouring it by direction
  // restates what the candles already say, in the one pair that fails a
  // colour-vision check.
  const volume: HistogramPoint[] = bars.map((bar) => ({
    time: bar.trade_date,
    value: bar.volume,
    tone: 'neutral',
  }));

  // A Set both de-duplicates and preserves the order the keys arrived in.
  const overlays: ChartLine[] = [...new Set(active.overlays)]
    .slice(0, MAX_ACTIVE_OVERLAYS)
    .map((key) => ({
      id: key,
      label: OVERLAY_LABEL[key],
      // By identity, never by position — see OVERLAY_SLOT.
      color: slots[OVERLAY_SLOT[key]],
      data: lineFor(bars, byDate, key),
    }));

  const oscillators = active.oscillators.map((key) => oscillatorPane(key, bars, byDate, slots));

  return { candles, volume, overlays, oscillators };
}
