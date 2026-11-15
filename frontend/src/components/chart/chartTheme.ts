/**
 * The chart's colours, per mode.
 *
 * These are **not** read through `theme.palette.*`, and that is deliberate: the
 * MUI theme runs on CSS variables, so `palette.profit.main` evaluates to the
 * string `var(--mui-palette-profit-main)`. A canvas cannot resolve that — it
 * would paint nothing — so the chart takes real values, sourced from the same
 * `theme/palette.ts` constants the rest of the app uses.
 *
 * **Light and dark are selected, not flipped.** Each mode has its own steps,
 * and both sets were checked with `scripts/validate_palette.js` from the
 * `dataviz` skill against this project's real card surface. Re-run it if any
 * value here changes rather than reasoning about the result:
 *
 *   node scripts/validate_palette.js "#3480fb,#bf8200,#d55181" \
 *     --mode dark --surface "#141922" --pairs all
 *   node scripts/validate_palette.js "#1565d8,#a86f00,#c2185b" \
 *     --mode light --surface "#ffffff" --pairs all
 *
 * Both report ALL CHECKS PASS. Worst all-pairs separation is ΔE 12.3 under
 * deuteranopia in dark and ΔE 10.0 in light, against a target of 8.
 *
 * One thing the validator says about this project that is worth keeping in
 * view: `profit` (#0f9d58) against `loss` (#e5484d) measures **ΔE 4.4 under
 * deuteranopia**, far below that target. Red-green is the classic
 * colour-vision collapse. That is why candles carry direction by fill as well
 * as hue — hollow up, filled down — and why volume takes a single neutral
 * instead of restating direction in exactly that pair.
 */
import { brand, market } from '@/theme/palette';

export type ColorScheme = 'light' | 'dark';

export interface ChartColors {
  /** The card the chart sits on. The validator's surface. */
  background: string;
  text: string;
  grid: string;
  border: string;
  crosshair: string;
  /** Up candles: outline only, so the body reads as hollow. */
  up: string;
  /** Down candles: filled solid. */
  down: string;
  /** Volume, and any other magnitude-only mark. One recessive neutral. */
  neutral: string;
  /** RSI's 30/70 guides and MACD's zero line. */
  reference: string;
  /**
   * The three overlay slots, in order. Assigned by indicator identity, never
   * by the order lines were switched on.
   */
  slots: readonly [string, string, string];
}

const DARK: ChartColors = {
  background: '#141922',
  text: 'rgba(255,255,255,0.68)',
  grid: 'rgba(255,255,255,0.06)',
  border: 'rgba(255,255,255,0.12)',
  crosshair: 'rgba(255,255,255,0.42)',
  up: market.profitMain,
  down: market.lossMain,
  neutral: 'rgba(255,255,255,0.30)',
  reference: 'rgba(255,255,255,0.24)',
  // brand[400], a validated amber, a validated magenta. brand[300] and a
  // brighter amber both sit above the dark lightness band and fail the check.
  slots: [brand[400], '#bf8200', '#d55181'],
};

const LIGHT: ChartColors = {
  background: '#ffffff',
  text: 'rgba(0,0,0,0.62)',
  grid: 'rgba(0,0,0,0.07)',
  border: 'rgba(0,0,0,0.14)',
  crosshair: 'rgba(0,0,0,0.45)',
  up: market.profitDark,
  down: market.lossDark,
  neutral: 'rgba(0,0,0,0.26)',
  reference: 'rgba(0,0,0,0.22)',
  slots: [brand[500], '#a86f00', '#c2185b'],
};

export function chartColors(scheme: ColorScheme): ChartColors {
  return scheme === 'dark' ? DARK : LIGHT;
}
