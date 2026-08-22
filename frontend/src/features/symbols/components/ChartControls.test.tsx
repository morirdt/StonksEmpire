/**
 * The controls, which are the part of the chart that has a DOM.
 *
 * Nothing here asserts on a rendered pixel, and nothing can: the chart itself
 * is a canvas. See `toChartSeries.test.ts` for the logic that stands in for it.
 */
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '@/test/utils';
import { ChartControls } from './ChartControls';
import {
  MAX_ACTIVE_OVERLAYS,
  OVERLAY_KEYS,
  type OscillatorKey,
  type OverlayKey,
} from '../lib/indicators';

function setup(overrides: Partial<React.ComponentProps<typeof ChartControls>> = {}) {
  const props = {
    range: '1Y' as const,
    overlays: [] as OverlayKey[],
    oscillators: [] as OscillatorKey[],
    onRangeChange: vi.fn(),
    onOverlaysChange: vi.fn(),
    onOscillatorsChange: vi.fn(),
    ...overrides,
  };
  renderWithProviders(<ChartControls {...props} />);
  return props;
}

describe('ChartControls', () => {
  it('reports the range the user picked', async () => {
    const props = setup();

    await userEvent.click(screen.getByRole('button', { name: '6M' }));

    expect(props.onRangeChange).toHaveBeenCalledWith('6M');
  });

  it('marks the current range as selected', () => {
    setup({ range: '2Y' });

    expect(screen.getByRole('button', { name: '2Y' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('adds an overlay that was off', async () => {
    const props = setup({ overlays: ['sma_20'] });

    await userEvent.click(screen.getByRole('button', { name: 'SMA 50' }));

    expect(props.onOverlaysChange).toHaveBeenCalledWith(['sma_20', 'sma_50']);
  });

  it('removes an overlay that was on', async () => {
    const props = setup({ overlays: ['sma_20', 'sma_50'] });

    await userEvent.click(screen.getByRole('button', { name: 'SMA 20' }));

    expect(props.onOverlaysChange).toHaveBeenCalledWith(['sma_50']);
  });

  it('offers exactly as many overlays as there are palette slots', () => {
    // Which is why no chip is ever blocked today: the cap is structural rather
    // than a runtime guard. This assertion is the tripwire for adding a fourth
    // overlay — at which point the toggle really does have to block, and the
    // palette needs a fourth validated colour it does not currently have.
    expect(OVERLAY_KEYS).toHaveLength(MAX_ACTIVE_OVERLAYS);
  });

  it('still lets an active overlay be switched off at the cap', async () => {
    // The failure this guards against once a fourth overlay exists: disabling
    // *every* chip at the cap, which leaves the user unable to change their
    // mind without a reload.
    const props = setup({ overlays: ['sma_20', 'sma_50', 'sma_200'] });

    await userEvent.click(screen.getByRole('button', { name: 'SMA 200' }));

    expect(props.onOverlaysChange).toHaveBeenCalledWith(['sma_20', 'sma_50']);
  });

  it('toggles an oscillator pane', async () => {
    const props = setup({ oscillators: ['rsi_14'] });

    await userEvent.click(screen.getByRole('button', { name: 'MACD (12, 26, 9)' }));

    expect(props.onOscillatorsChange).toHaveBeenCalledWith(['rsi_14', 'macd']);
  });

  it('does not cap the oscillator panes', async () => {
    // They stack rather than overlap, so there is no readability ceiling and
    // no shared palette to run out of.
    const props = setup({ oscillators: ['rsi_14', 'macd'] });

    expect(screen.getByRole('button', { name: 'RSI (14)' })).not.toHaveClass('Mui-disabled');
    await userEvent.click(screen.getByRole('button', { name: 'RSI (14)' }));
    expect(props.onOscillatorsChange).toHaveBeenCalledWith(['macd']);
  });
});
