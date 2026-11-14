/**
 * The series shape the chart wrapper consumes.
 *
 * Deliberately not the API's shape and not the library's. It sits between them
 * so that `CandlestickChart` stays a dumb renderer — it knows how to draw
 * candles, lines, and histograms in panes, and nothing about tickers,
 * indicators, or what an RSI is. Phase 6's equity curve reuses it unchanged.
 *
 * Two rules are encoded in these types rather than left to convention:
 *
 * - **A missing value is an absent `value`, never a zero.** A zero plots a line
 *   to the bottom of the chart and looks like a crash; the library treats a
 *   point with no `value` as whitespace and draws a gap, which is the truth.
 * - **Marks carry a *tone*, not a colour.** The theme owns what `profit` and
 *   `loss` look like in each mode, and components must never reach for a green
 *   or red literal. Only overlays name a colour, because their colour is
 *   assigned by indicator identity and has to stay fixed as lines are toggled.
 */

/** `YYYY-MM-DD`. The API's `trade_date`, passed through unparsed. */
export type ChartTime = string;

export interface Candle {
  time: ChartTime;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** `value` absent means "no value here" — the series gaps rather than dips. */
export interface LinePoint {
  time: ChartTime;
  value?: number;
}

export type MarkTone = 'profit' | 'loss' | 'neutral';

export interface HistogramPoint {
  time: ChartTime;
  value?: number;
  tone?: MarkTone;
}

export interface ChartLine {
  /** Stable across renders; changing it rebuilds the series. */
  id: string;
  label: string;
  color: string;
  data: LinePoint[];
}

/**
 * One lower pane. RSI and MACD each get their own; they never share, because
 * their scales are unrelated and stacking them would be a dual axis by another
 * name.
 */
export interface OscillatorPane {
  id: string;
  label: string;
  lines: ChartLine[];
  histogram?: { id: string; data: HistogramPoint[] };
  /** A fixed range, e.g. RSI's 0–100. Auto-scaling hides what RSI is read for. */
  fixedScale?: { min: number; max: number };
  /** Horizontal guides, e.g. RSI 30/70 or MACD's zero line. */
  referenceLines?: number[];
}

export interface ChartSeriesData {
  candles: Candle[];
  /** One neutral hue, in its own pane. Never a second y-axis on price. */
  volume: HistogramPoint[];
  /** At most three, coloured by identity. */
  overlays: ChartLine[];
  oscillators: OscillatorPane[];
}
