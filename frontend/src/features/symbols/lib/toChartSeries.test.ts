/**
 * The one place the canvas coverage gap is paid back.
 *
 * `CandlestickChart` renders to a canvas, so there is no DOM for Testing
 * Library to assert on and nothing here checks a pixel. That makes this pure
 * function — everything between the API's rows and the library's series format
 * — the piece worth testing thoroughly, because every bug the chart can have
 * that is not a rendering bug is a bug in here.
 */
import { describe, expect, it } from 'vitest';
import { toChartSeries } from './toChartSeries';
import type { Bar, Indicator } from '../api/useSymbolChart';

const SLOTS = ['slot-1', 'slot-2', 'slot-3'] as const;

function bar(trade_date: string, close: number, volume = 1_000): Bar {
  return {
    trade_date,
    open: String(close - 1),
    high: String(close + 2),
    low: String(close - 2),
    close: String(close),
    volume,
    is_adjusted: true,
  };
}

function indicator(trade_date: string, values: Partial<Omit<Indicator, 'trade_date'>>): Indicator {
  return { trade_date, ...values };
}

const NOTHING_ACTIVE = { overlays: [], oscillators: [] };

describe('toChartSeries', () => {
  it('turns bars into candles in the order they arrived', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10), bar('2026-01-05', 12)],
      [],
      NOTHING_ACTIVE,
      SLOTS,
    );

    expect(series.candles).toEqual([
      { time: '2026-01-02', open: 9, high: 12, low: 8, close: 10 },
      { time: '2026-01-05', open: 11, high: 14, low: 10, close: 12 },
    ]);
  });

  it('gives volume one neutral tone rather than restating direction', () => {
    const series = toChartSeries([bar('2026-01-02', 10, 5_000)], [], NOTHING_ACTIVE, SLOTS);

    expect(series.volume).toEqual([{ time: '2026-01-02', value: 5_000, tone: 'neutral' }]);
  });

  it('produces nothing at all from an empty window', () => {
    const series = toChartSeries([], [], { overlays: ['sma_20'], oscillators: ['rsi_14'] }, SLOTS);

    expect(series.candles).toEqual([]);
    expect(series.volume).toEqual([]);
    expect(series.overlays).toEqual([]);
    expect(series.oscillators).toEqual([]);
  });

  // ---------------------------------------------------------------- nulls

  it('leaves a leading null as a gap, never as a zero', () => {
    // Every real series looks like this: a 200-day average has no value for
    // its first 199 bars. A zero would plot a line to the bottom of the chart
    // and read as a crash.
    const series = toChartSeries(
      [bar('2026-01-02', 10), bar('2026-01-05', 12), bar('2026-01-06', 14)],
      [
        indicator('2026-01-02', { sma_20: null }),
        indicator('2026-01-05', { sma_20: null }),
        indicator('2026-01-06', { sma_20: '12.5' }),
      ],
      { overlays: ['sma_20'], oscillators: [] },
      SLOTS,
    );

    expect(series.overlays[0]!.data).toEqual([
      { time: '2026-01-02' },
      { time: '2026-01-05' },
      { time: '2026-01-06', value: 12.5 },
    ]);
  });

  it('emits a point for every bar even where the indicator row is missing', () => {
    // The two endpoints are separate round trips. If one is a row short the
    // chart must still gap rather than shift every later value left.
    const series = toChartSeries(
      [bar('2026-01-02', 10), bar('2026-01-05', 12)],
      [indicator('2026-01-05', { sma_20: '11' })],
      { overlays: ['sma_20'], oscillators: [] },
      SLOTS,
    );

    expect(series.overlays[0]!.data).toEqual([
      { time: '2026-01-02' },
      { time: '2026-01-05', value: 11 },
    ]);
  });

  it('aligns indicators by trade date rather than by position', () => {
    // The backend guarantees the two windows match. Joining on the date anyway
    // means a skew shows up as a gap instead of as silently wrong values.
    const series = toChartSeries(
      [bar('2026-01-02', 10), bar('2026-01-05', 12)],
      [indicator('2026-01-05', { sma_20: '11' }), indicator('2026-01-02', { sma_20: '9' })],
      { overlays: ['sma_20'], oscillators: [] },
      SLOTS,
    );

    expect(series.overlays[0]!.data).toEqual([
      { time: '2026-01-02', value: 9 },
      { time: '2026-01-05', value: 11 },
    ]);
  });

  it('drops an indicator row that has no bar behind it', () => {
    const series = toChartSeries(
      [bar('2026-01-05', 12)],
      [indicator('2026-01-02', { sma_20: '9' }), indicator('2026-01-05', { sma_20: '11' })],
      { overlays: ['sma_20'], oscillators: [] },
      SLOTS,
    );

    expect(series.overlays[0]!.data).toEqual([{ time: '2026-01-05', value: 11 }]);
  });

  // -------------------------------------------------------------- overlays

  it('colours an overlay by its identity, not by the order it was enabled', () => {
    const first = toChartSeries(
      [bar('2026-01-02', 10)],
      [],
      {
        overlays: ['sma_50'],
        oscillators: [],
      },
      SLOTS,
    );
    const third = toChartSeries(
      [bar('2026-01-02', 10)],
      [],
      {
        overlays: ['sma_200', 'sma_20', 'sma_50'],
        oscillators: [],
      },
      SLOTS,
    );

    const alone = first.overlays[0]!;
    const alongside = third.overlays.find((line) => line.id === 'sma_50');

    expect(alone.color).toBe('slot-2');
    expect(alongside?.color).toBe('slot-2');
  });

  it('keeps every other line the same colour when one is switched off', () => {
    // The failure this prevents: colour that follows position repaints the
    // survivors every time a line is toggled, so the chart you learned to read
    // means something different a click later.
    const before = toChartSeries(
      [bar('2026-01-02', 10)],
      [],
      {
        overlays: ['sma_20', 'sma_50', 'sma_200'],
        oscillators: [],
      },
      SLOTS,
    );
    const after = toChartSeries(
      [bar('2026-01-02', 10)],
      [],
      {
        overlays: ['sma_50', 'sma_200'],
        oscillators: [],
      },
      SLOTS,
    );

    const colourOf = (series: typeof before, id: string) =>
      series.overlays.find((line) => line.id === id)?.color;

    expect(colourOf(after, 'sma_50')).toBe(colourOf(before, 'sma_50'));
    expect(colourOf(after, 'sma_200')).toBe(colourOf(before, 'sma_200'));
  });

  it('draws at most three overlays however many were asked for', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [],
      {
        overlays: ['sma_20', 'sma_50', 'sma_200', 'sma_20'],
        oscillators: [],
      },
      SLOTS,
    );

    expect(series.overlays).toHaveLength(3);
  });

  it('labels overlays for the legend', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [],
      {
        overlays: ['sma_200'],
        oscillators: [],
      },
      SLOTS,
    );

    expect(series.overlays[0]!.label).toBe('SMA 200');
  });

  // ----------------------------------------------------------- oscillators

  it('gives RSI its own pane with a fixed scale and reference levels', () => {
    // An auto-scaled RSI hides the only thing it is read for.
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [indicator('2026-01-02', { rsi_14: '62.5' })],
      { overlays: [], oscillators: ['rsi_14'] },
      SLOTS,
    );

    const pane = series.oscillators[0]!;
    expect(pane.id).toBe('rsi_14');
    expect(pane.fixedScale).toEqual({ min: 0, max: 100 });
    expect(pane.referenceLines).toEqual([30, 70]);
    expect(pane.lines).toHaveLength(1);
    expect(pane.lines[0]!.data).toEqual([{ time: '2026-01-02', value: 62.5 }]);
  });

  it('gives MACD a pane with two lines and a histogram around zero', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10), bar('2026-01-05', 12)],
      [
        indicator('2026-01-02', { macd: '1.5', macd_signal: '1.2', macd_histogram: '0.3' }),
        indicator('2026-01-05', { macd: '-0.5', macd_signal: '0.1', macd_histogram: '-0.6' }),
      ],
      { overlays: [], oscillators: ['macd'] },
      SLOTS,
    );

    const pane = series.oscillators[0]!;
    expect(pane.lines.map((line) => line.id)).toEqual(['macd', 'macd_signal']);
    expect(pane.lines[0]!.color).toBe('slot-1');
    expect(pane.lines[1]!.color).toBe('slot-2');
    expect(pane.referenceLines).toEqual([0]);
    expect(pane.histogram?.data).toEqual([
      { time: '2026-01-02', value: 0.3, tone: 'profit' },
      { time: '2026-01-05', value: -0.6, tone: 'loss' },
    ]);
  });

  it('gives a zero histogram bar a neutral tone rather than calling it a gain', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [indicator('2026-01-02', { macd: '1', macd_signal: '1', macd_histogram: '0' })],
      { overlays: [], oscillators: ['macd'] },
      SLOTS,
    );

    expect(series.oscillators[0]!.histogram?.data).toEqual([
      { time: '2026-01-02', value: 0, tone: 'neutral' },
    ]);
  });

  it('keeps the panes in the order the oscillators were listed', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [indicator('2026-01-02', { rsi_14: '50', macd: '1' })],
      { overlays: [], oscillators: ['macd', 'rsi_14'] },
      SLOTS,
    );

    expect(series.oscillators.map((pane) => pane.id)).toEqual(['macd', 'rsi_14']);
  });

  it('still produces a pane when every value in it is null', () => {
    // A newly backfilled symbol looks exactly like this for its first bars, and
    // an absent pane would read as "RSI is off" rather than "not enough history".
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [indicator('2026-01-02', { rsi_14: null })],
      { overlays: [], oscillators: ['rsi_14'] },
      SLOTS,
    );

    expect(series.oscillators[0]!.lines[0]!.data).toEqual([{ time: '2026-01-02' }]);
  });

  it('ignores an unparseable value rather than plotting NaN', () => {
    const series = toChartSeries(
      [bar('2026-01-02', 10)],
      [indicator('2026-01-02', { sma_20: 'not-a-number' })],
      { overlays: ['sma_20'], oscillators: [] },
      SLOTS,
    );

    expect(series.overlays[0]!.data).toEqual([{ time: '2026-01-02' }]);
  });
});
