/**
 * The imperative wrapper around `lightweight-charts`, and the only place in the
 * app that touches it.
 *
 * It lives in `components/` rather than in a feature because Phase 6's equity
 * curve needs the same wrapper and features may not import each other. It
 * therefore knows how to draw candles, lines, and histograms in stacked panes —
 * and nothing about tickers, indicators, or what an RSI is. Everything
 * symbol-shaped happens before the data reaches it.
 *
 * Three things about the lifecycle are worth knowing, because they are the ways
 * an imperative canvas library in React goes wrong:
 *
 * 1. **The chart is created once and disposed on unmount.** `chart.remove()`
 *    detaches listeners and frees the canvas; skipping it leaks one chart per
 *    navigation, and React 19's strict double-mount makes that immediate.
 * 2. **Series are reconciled, not rebuilt, whenever the pane layout is
 *    unchanged.** Rebuilding resets the zoom, so toggling a moving average
 *    would throw away the window the user had panned to. Adding or removing a
 *    *pane* does force a rebuild, because pane indices shift underneath the
 *    remaining series when one disappears.
 * 3. **Width comes from a ResizeObserver, not from a resize listener.** The
 *    chart is inside a flex layout that changes width without the window ever
 *    resizing.
 *
 * There is no test file beside this component **on purpose**: it renders to a
 * canvas, so there is no DOM for Testing Library to assert on and nothing here
 * can be checked without pixel comparison. The shaping logic it consumes is
 * tested directly instead, in `features/symbols/lib/toChartSeries.test.ts`.
 */
import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  TickMarkType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type Time,
} from 'lightweight-charts';
import Box from '@mui/material/Box';
import type { ChartColors } from './chartTheme';
import type { ChartSeriesData, HistogramPoint, LinePoint, MarkTone } from './types';

export interface CandlestickChartProps {
  data: ChartSeriesData;
  colors: ChartColors;
  /** Total height of every pane together, in pixels. */
  height?: number;
  /**
   * The bar under the crosshair, or null when the pointer leaves.
   *
   * Reported rather than drawn: the tooltip is rendered as HTML by the page, so
   * it is selectable, themeable, and visible to a screen reader — none of which
   * is true of text painted into a canvas.
   */
  onHoverTimeChange?: (time: string | null) => void;
  /** Labels the canvas for assistive technology, which cannot see into it. */
  ariaLabel?: string;
}

/** Pane 0 is price, pane 1 is volume, oscillators start at 2. */
const PRICE_PANE = 0;
const VOLUME_PANE = 1;
const FIRST_OSCILLATOR_PANE = 2;

/** Relative pane heights. Price dominates; volume is a supporting read. */
const PRICE_STRETCH = 5;
const VOLUME_STRETCH = 1.5;
const OSCILLATOR_STRETCH = 2;

type AnySeries = ISeriesApi<SeriesType, Time>;

interface SeriesSpec {
  id: string;
  kind: 'candles' | 'line' | 'histogram';
  pane: number;
  color?: string;
  fixedScale?: { min: number; max: number };
  referenceLines?: number[];
}

/**
 * Everything the chart should contain, flattened to one addressable list.
 *
 * Deriving this from the data — rather than building series inline — is what
 * makes reconciliation possible: the ids here are stable across renders, so a
 * survivor can be told apart from a newcomer.
 */
function describe(data: ChartSeriesData): SeriesSpec[] {
  const specs: SeriesSpec[] = [
    { id: 'candles', kind: 'candles', pane: PRICE_PANE },
    { id: 'volume', kind: 'histogram', pane: VOLUME_PANE },
  ];

  for (const line of data.overlays) {
    specs.push({ id: `overlay:${line.id}`, kind: 'line', pane: PRICE_PANE, color: line.color });
  }

  data.oscillators.forEach((pane, index) => {
    const paneIndex = FIRST_OSCILLATOR_PANE + index;
    for (const line of pane.lines) {
      specs.push({
        id: `${pane.id}:${line.id}`,
        kind: 'line',
        pane: paneIndex,
        color: line.color,
        fixedScale: pane.fixedScale,
        referenceLines: pane.lines[0]?.id === line.id ? pane.referenceLines : undefined,
      });
    }
    if (pane.histogram) {
      specs.push({ id: `${pane.id}:histogram`, kind: 'histogram', pane: paneIndex });
    }
  });

  return specs;
}

/** Which panes exist, in order. A change here forces a full rebuild. */
function paneSignature(data: ChartSeriesData): string {
  return data.oscillators.map((pane) => pane.id).join('|');
}

function toneColor(tone: MarkTone | undefined, colors: ChartColors): string {
  if (tone === 'profit') return colors.up;
  if (tone === 'loss') return colors.down;
  return colors.neutral;
}

/**
 * A point with no `value` is passed through as whitespace, which the library
 * draws as a gap. Substituting a zero would draw a line to the bottom of the
 * pane — the failure mode every leading indicator window would otherwise have.
 */
function histogramData(points: HistogramPoint[], colors: ChartColors) {
  return points.map((point) =>
    point.value === undefined
      ? { time: point.time as Time }
      : { time: point.time as Time, value: point.value, color: toneColor(point.tone, colors) },
  );
}

function lineData(points: LinePoint[]) {
  return points.map((point) =>
    point.value === undefined
      ? { time: point.time as Time }
      : { time: point.time as Time, value: point.value },
  );
}

function applySeriesColors(series: AnySeries, spec: SeriesSpec, colors: ChartColors): void {
  if (spec.kind === 'candles') {
    series.applyOptions({
      // Hollow up, filled down. The second encoding is required, not
      // decorative: profit and loss are ΔE 4.4 apart under deuteranopia, so a
      // chart that carries direction in hue alone is unreadable for a
      // substantial minority. See chartTheme.ts.
      upColor: 'rgba(0,0,0,0)',
      borderUpColor: colors.up,
      wickUpColor: colors.up,
      downColor: colors.down,
      borderDownColor: colors.down,
      wickDownColor: colors.down,
      borderVisible: true,
    });
    return;
  }
  if (spec.kind === 'line') {
    series.applyOptions({ color: spec.color, lineWidth: 2 });
  }
}

function createSeries(chart: IChartApi, spec: SeriesSpec, colors: ChartColors): AnySeries {
  if (spec.kind === 'candles') {
    const series = chart.addSeries(CandlestickSeries, {}, spec.pane);
    applySeriesColors(series, spec, colors);
    return series;
  }

  if (spec.kind === 'histogram') {
    return chart.addSeries(
      HistogramSeries,
      { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false },
      spec.pane,
    );
  }

  const series = chart.addSeries(
    LineSeries,
    {
      lineWidth: 2,
      color: spec.color,
      priceLineVisible: false,
      lastValueVisible: false,
      // The dot the crosshair puts on this line, so the readout above the chart
      // and the line it is quoting agree about which bar is being read.
      crosshairMarkerVisible: true,
    },
    spec.pane,
  );

  if (spec.fixedScale) {
    // An auto-scaled RSI hides the only thing it is read for. The provider form
    // is the library's way of pinning a range without pinning the pane.
    const { min, max } = spec.fixedScale;
    series.applyOptions({
      autoscaleInfoProvider: () => ({ priceRange: { minValue: min, maxValue: max } }),
    });
    // Without this the pane pads the fixed range out to 0-120, and a scale
    // labelled 0-120 is no longer the scale RSI is read against.
    series.priceScale().applyOptions({ scaleMargins: { top: 0.04, bottom: 0.04 } });
  }

  for (const price of spec.referenceLines ?? []) {
    series.createPriceLine({
      price,
      color: colors.reference,
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: false,
      title: '',
    });
  }

  return series;
}

/**
 * The library's `Time` narrowed back to the `YYYY-MM-DD` this wrapper speaks.
 *
 * Everything fed in is a date string, so in practice this always takes the
 * first branch — but `Time` also admits a UTC timestamp and a
 * `{year, month, day}` object, and stringifying either of those yields
 * `[object Object]` or a second count. The crosshair hands back whatever the
 * library holds, so the conversion has to be total rather than assumed.
 */
function timeToIsoDate(time: Time): string | null {
  if (typeof time === 'string') return time;
  if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10);
  if (typeof time === 'object' && 'year' in time) {
    const month = String(time.month).padStart(2, '0');
    const day = String(time.day).padStart(2, '0');
    return `${time.year}-${month}-${day}`;
  }
  return null;
}

/**
 * Month and year ticks, never a bare day number.
 *
 * The library's default prints the day of the month for ticks it does not
 * promote to a month label, so a daily axis reads `Sep Oct Nov 2 2026 Feb 3` —
 * where `2` and `3` are the 2nd of December and the 3rd of March. Alongside
 * month names that is unreadable, so a day tick gets its month with it.
 */
function formatTick(time: Time, tickMarkType: TickMarkType, locale: string): string {
  const iso = timeToIsoDate(time);
  const date = new Date(`${iso}T00:00:00Z`);
  if (iso === null || Number.isNaN(date.getTime())) return '';
  if (tickMarkType === TickMarkType.Year) return String(date.getUTCFullYear());
  if (tickMarkType === TickMarkType.Month) {
    return date.toLocaleDateString(locale, { month: 'short', timeZone: 'UTC' });
  }
  return date.toLocaleDateString(locale, { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

function chartOptions(colors: ChartColors) {
  return {
    layout: {
      background: { color: colors.background },
      textColor: colors.text,
      attributionLogo: false,
      panes: { separatorColor: colors.border, enableResize: false },
    },
    grid: {
      vertLines: { color: colors.grid },
      horzLines: { color: colors.grid },
    },
    crosshair: {
      // A crosshair by default: 500 candles cannot be read to a precise value
      // without one.
      mode: 1 as const,
      vertLine: { color: colors.crosshair, labelBackgroundColor: colors.border },
      horzLine: { color: colors.crosshair, labelBackgroundColor: colors.border },
    },
    rightPriceScale: { borderColor: colors.border },
    timeScale: {
      borderColor: colors.border,
      timeVisible: false,
      tickMarkFormatter: formatTick,
    },
  };
}

export function CandlestickChart({
  data,
  colors,
  height = 520,
  onHoverTimeChange,
  ariaLabel,
}: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef(new Map<string, AnySeries>());
  const layoutRef = useRef<string>('');
  const hoverRef = useRef(onHoverTimeChange);

  // Through a ref rather than a dependency: the crosshair subscription is made
  // once, when the chart is created, and a new callback identity on every
  // render must not tear the chart down. Assigned in an effect, because writing
  // a ref during render is not safe under concurrent rendering.
  useEffect(() => {
    hoverRef.current = onHoverTimeChange;
  }, [onHoverTimeChange]);

  // --- lifecycle: created once, disposed on unmount -------------------------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const series = seriesRef.current;

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      autoSize: false,
    });
    chartRef.current = chart;

    chart.subscribeCrosshairMove((param) => {
      hoverRef.current?.(param.time === undefined ? null : timeToIsoDate(param.time));
    });

    // The chart lives in a flex layout that resizes without the window doing so.
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      series.clear();
      layoutRef.current = '';
    };
    // Height is applied by its own effect; recreating the chart to resize it
    // would throw away the user's zoom.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions({ height });
  }, [height]);

  useEffect(() => {
    chartRef.current?.applyOptions(chartOptions(colors));
  }, [colors]);

  // --- series: reconciled where possible, rebuilt when panes change ---------
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const specs = describe(data);
    const signature = paneSignature(data);
    const series = seriesRef.current;

    // Pane indices shift under the survivors when a pane disappears, so a pane
    // change is the one case that cannot be reconciled in place.
    if (signature !== layoutRef.current) {
      for (const existing of series.values()) chart.removeSeries(existing);
      series.clear();
      layoutRef.current = signature;
    }

    const wanted = new Set(specs.map((spec) => spec.id));
    for (const [id, existing] of series) {
      if (!wanted.has(id)) {
        chart.removeSeries(existing);
        series.delete(id);
      }
    }

    let added = false;
    for (const spec of specs) {
      const existing = series.get(spec.id);
      if (existing) {
        applySeriesColors(existing, spec, colors);
      } else {
        series.set(spec.id, createSeries(chart, spec, colors));
        added = true;
      }
    }

    const panes = chart.panes();
    panes[PRICE_PANE]?.setStretchFactor(PRICE_STRETCH);
    panes[VOLUME_PANE]?.setStretchFactor(VOLUME_STRETCH);
    for (let i = FIRST_OSCILLATOR_PANE; i < panes.length; i += 1) {
      panes[i]?.setStretchFactor(OSCILLATOR_STRETCH);
    }

    // --- data ---------------------------------------------------------------
    series.get('candles')?.setData(data.candles);
    series.get('volume')?.setData(histogramData(data.volume, colors));

    for (const line of data.overlays) {
      series.get(`overlay:${line.id}`)?.setData(lineData(line.data));
    }
    for (const pane of data.oscillators) {
      for (const line of pane.lines) {
        series.get(`${pane.id}:${line.id}`)?.setData(lineData(line.data));
      }
      if (pane.histogram) {
        series.get(`${pane.id}:histogram`)?.setData(histogramData(pane.histogram.data, colors));
      }
    }

    // Only on a structural change. Doing it on every data update would yank the
    // view back whenever a poll landed.
    if (added) chart.timeScale().fitContent();
  }, [data, colors]);

  return (
    <Box
      ref={containerRef}
      role="img"
      aria-label={ariaLabel ?? 'Price chart'}
      sx={{ width: '100%', height, '& canvas': { display: 'block' } }}
    />
  );
}
