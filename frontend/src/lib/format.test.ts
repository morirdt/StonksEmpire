import { describe, expect, it } from 'vitest';
import {
  EM_DASH,
  changeTone,
  formatAge,
  formatChange,
  formatPercent,
  formatPrice,
  formatVolume,
  toNumber,
} from './format';

describe('toNumber', () => {
  it('parses the strings the API actually sends', () => {
    // Prices arrive as strings precisely so no client floats them by accident.
    expect(toNumber('123.456789')).toBeCloseTo(123.456789);
  });

  it('returns null for anything missing or unparseable', () => {
    expect(toNumber(null)).toBeNull();
    expect(toNumber(undefined)).toBeNull();
    expect(toNumber('not a price')).toBeNull();
  });

  it('keeps zero rather than treating it as absent', () => {
    expect(toNumber('0')).toBe(0);
  });
});

describe('formatting', () => {
  it('shows prices to two places', () => {
    expect(formatPrice('123.4')).toBe('123.40');
  });

  it('renders a missing value as a dash, never as zero', () => {
    expect(formatPrice(null)).toBe(EM_DASH);
    expect(formatPercent(null)).toBe(EM_DASH);
    expect(formatVolume(null)).toBe(EM_DASH);
  });

  it('signs changes so a gain reads as one without extra markup', () => {
    expect(formatChange('1.5')).toBe('+1.50');
    expect(formatChange('-1.5')).toBe('-1.50');
    expect(formatPercent('2.25')).toBe('+2.25%');
  });

  it('compacts volume, which is read at a glance', () => {
    expect(formatVolume(12_340_000)).toBe('12.3M');
  });
});

describe('changeTone', () => {
  it('maps direction to a palette token, not a colour', () => {
    expect(changeTone('1.5')).toBe('profit');
    expect(changeTone('-1.5')).toBe('loss');
  });

  it('treats flat and unknown as the same neutral case', () => {
    expect(changeTone('0')).toBe('flat');
    expect(changeTone(null)).toBe('flat');
  });
});

describe('formatAge', () => {
  const now = Date.parse('2026-08-21T12:00:00Z');

  it('describes how long ago we last asked', () => {
    expect(formatAge('2026-08-21T11:59:30Z', now)).toBe('just now');
    expect(formatAge('2026-08-21T11:30:00Z', now)).toBe('30m ago');
    expect(formatAge('2026-08-21T09:00:00Z', now)).toBe('3h ago');
    expect(formatAge('2026-08-19T12:00:00Z', now)).toBe('2d ago');
  });

  it('does not render a future timestamp as negative', () => {
    // Clock skew between the server and the browser is normal and must not
    // produce "-1m ago".
    expect(formatAge('2026-08-21T12:00:30Z', now)).toBe('just now');
  });

  it('falls back to a dash when there is no timestamp', () => {
    expect(formatAge(null, now)).toBe(EM_DASH);
    expect(formatAge('nonsense', now)).toBe(EM_DASH);
  });
});
