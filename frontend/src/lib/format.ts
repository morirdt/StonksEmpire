/**
 * Number and date formatting for market data.
 *
 * Prices cross the wire as **strings**, not numbers: the backend stores them as
 * NUMERIC and serialises Decimal as a string precisely so that no JavaScript
 * client silently rounds them through a float. Parsing happens here, at the
 * display boundary, and nowhere else — a price should never be turned into a
 * number in order to be passed around.
 */

/** `null` for anything unparseable, so a missing price renders as a dash. */
export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const priceFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const percentFormatter = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: 'exceptZero',
});

const compactFormatter = new Intl.NumberFormat(undefined, {
  notation: 'compact',
  maximumFractionDigits: 1,
});

export const EM_DASH = '—';

export function formatPrice(value: string | number | null | undefined): string {
  const parsed = toNumber(value);
  return parsed === null ? EM_DASH : priceFormatter.format(parsed);
}

/** Signed, so a gain reads `+1.24` and a loss `-1.24` without extra markup. */
export function formatChange(value: string | number | null | undefined): string {
  const parsed = toNumber(value);
  return parsed === null ? EM_DASH : percentFormatter.format(parsed);
}

export function formatPercent(value: string | number | null | undefined): string {
  const parsed = toNumber(value);
  return parsed === null ? EM_DASH : `${percentFormatter.format(parsed)}%`;
}

/** Volume is read at a glance, so 12.3M beats 12,340,000. */
export function formatVolume(value: number | null | undefined): string {
  return value === null || value === undefined ? EM_DASH : compactFormatter.format(value);
}

/**
 * Which palette token a change belongs to.
 *
 * Returns a token name rather than a colour: `profit`/`loss` are real palette
 * entries, and components must never reach for a green or red literal.
 */
export function changeTone(value: string | number | null | undefined): 'profit' | 'loss' | 'flat' {
  const parsed = toNumber(value);
  if (parsed === null || parsed === 0) return 'flat';
  return parsed > 0 ? 'profit' : 'loss';
}

/**
 * How stale a quote is, in words.
 *
 * `fetched_at` is when we last asked, which is the honest thing to show: a
 * price from Friday afternoon is not broken on a Sunday, and a label that says
 * "2 days ago" explains that better than a timestamp nobody reads.
 */
export function formatAge(isoTimestamp: string | null | undefined, now = Date.now()): string {
  if (!isoTimestamp) return EM_DASH;
  const then = Date.parse(isoTimestamp);
  if (Number.isNaN(then)) return EM_DASH;

  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86_400)}d ago`;
}
