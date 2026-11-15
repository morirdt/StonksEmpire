/**
 * The indicator catalogue: what can be drawn, where, and under what name.
 *
 * One module so the toggles, the legend, the table view, and the series shaper
 * cannot drift apart on labels or on which pane something belongs to.
 */
import type { components } from '@/lib/api/schema';

export type OverlayKey = components['schemas']['PriceOverlay'];
export type OscillatorKey = components['schemas']['Oscillator'];

/**
 * Which palette slot an overlay always uses.
 *
 * Fixed per identity, never per position: `sma_50` is slot 2 whether it is the
 * only line on the chart or the third one enabled. Colour that followed the
 * order things were switched on would repaint every other line each time one
 * was toggled off.
 *
 * There are exactly as many overlays as there are slots, which is why this map
 * is total and collision-free. The backend enum is narrowed to match, and for
 * the same reason: a fourth overlay identity would have to share a colour with
 * one of these permanently.
 */
export const OVERLAY_SLOT: Record<OverlayKey, 0 | 1 | 2> = {
  sma_20: 0,
  sma_50: 1,
  sma_200: 2,
};

export const OVERLAY_LABEL: Record<OverlayKey, string> = {
  sma_20: 'SMA 20',
  sma_50: 'SMA 50',
  sma_200: 'SMA 200',
};

export const OSCILLATOR_LABEL: Record<OscillatorKey, string> = {
  rsi_14: 'RSI (14)',
  macd: 'MACD (12, 26, 9)',
};

/** The order the toggles are listed in — shortest average first. */
export const OVERLAY_KEYS = Object.keys(OVERLAY_LABEL) as OverlayKey[];
export const OSCILLATOR_KEYS = Object.keys(OSCILLATOR_LABEL) as OscillatorKey[];

/**
 * Beyond three, lines over candles stop being readable whatever the palette.
 * Enforced in the toggle UI, in the API schema, and once more when shaping —
 * a preference saved by an older client must still draw.
 */
export const MAX_ACTIVE_OVERLAYS = 3;

/** RSI is read for these two levels; auto-scaling the pane hides them. */
export const RSI_REFERENCE_LEVELS = [30, 70];
export const RSI_SCALE = { min: 0, max: 100 };
