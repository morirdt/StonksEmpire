/**
 * Semantic colour tokens.
 *
 * `profit` / `loss` are first-class palette entries rather than green/red
 * literals scattered through components: the meaning is fixed even if the
 * colour changes, and a future colour-blind-safe mode is a one-file edit.
 */

export const brand = {
  50: '#e8f2ff',
  100: '#c9dfff',
  200: '#96c1ff',
  300: '#5f9dff',
  400: '#3480fb',
  500: '#1565d8',
  600: '#0f4fae',
  700: '#0b3d87',
  800: '#082c62',
  900: '#051c3f',
} as const;

/** Green up / red down. Tuned for contrast on both surfaces. */
export const market = {
  profitLight: '#00382b',
  profitMain: '#0f9d58',
  profitDark: '#0b7a44',
  lossLight: '#3d0d10',
  lossMain: '#e5484d',
  lossDark: '#b8383c',
} as const;
