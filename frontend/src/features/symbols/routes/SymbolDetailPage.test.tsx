/**
 * The page's states and wiring.
 *
 * **Nothing here asserts on the chart itself, and nothing can.**
 * `lightweight-charts` renders to a canvas, so there is no DOM for Testing
 * Library to see — no candles, no lines, no axis labels. The wrapper is
 * therefore stubbed below, and this file covers the parts around it: the range
 * selector, the indicator toggles, the crosshair readout, and the empty,
 * loading, and error states.
 *
 * The gap that leaves is real and deliberate. It is paid back by
 * `../lib/toChartSeries.test.ts`, which tests every transformation between the
 * API's rows and the series the chart draws — the whole of what the chart does
 * apart from drawing. Playwright arrives in Phase 7 and closes the rest.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as ApiClientModule from '@/lib/api/client';
import { ApiError } from '@/lib/api/client';
import { renderWithProviders } from '@/test/utils';
import { SymbolDetailPage } from './SymbolDetailPage';

const { apiGet, apiPut } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPut: vi.fn() }));

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ApiClientModule>();
  return { ...actual, api: { GET: apiGet, PUT: apiPut } };
});

/** The canvas stub. It reports what it was handed so the wiring is testable. */
vi.mock('@/components/chart/CandlestickChart', () => ({
  CandlestickChart: ({ data }: { data: { candles: unknown[]; overlays: { id: string }[] } }) => (
    <div
      data-testid="chart"
      data-candles={data.candles.length}
      data-overlays={data.overlays.map((line) => line.id).join(',')}
    />
  ),
}));

const SYMBOL = {
  id: 'sym-1',
  ticker: 'AAPL',
  name: 'Apple Inc.',
  exchange: 'NASDAQ',
  asset_type: 'common_stock',
  currency: 'USD',
  is_active: true,
};

const PREFERENCES = {
  default_range: '1Y',
  active_overlays: ['sma_20'],
  active_oscillators: ['rsi_14'],
};

function bar(trade_date: string, close: number) {
  return {
    trade_date,
    open: String(close - 1),
    high: String(close + 1),
    low: String(close - 2),
    close: String(close),
    volume: 1_000_000,
    is_adjusted: true,
  };
}

const BARS = [bar('2026-08-19', 210), bar('2026-08-20', 212), bar('2026-08-21', 215)];
const INDICATORS = BARS.map((row) => ({ trade_date: row.trade_date, sma_20: '211', rsi_14: '58' }));

/** Routed by path so no test depends on the order the queries happen to fire. */
function stubGet({
  bars = BARS,
  indicators = INDICATORS,
  preferences = PREFERENCES,
  symbolError,
  barsError,
}: {
  bars?: unknown[];
  indicators?: unknown[];
  preferences?: unknown;
  symbolError?: Error;
  barsError?: Error;
} = {}) {
  apiGet.mockImplementation((path: string) => {
    if (path === '/api/v1/symbols/{ticker}') {
      return symbolError ? Promise.reject(symbolError) : Promise.resolve({ data: SYMBOL });
    }
    if (path === '/api/v1/symbols/{ticker}/bars') {
      return barsError
        ? Promise.reject(barsError)
        : Promise.resolve({ data: { ticker: 'AAPL', bars } });
    }
    if (path === '/api/v1/symbols/{ticker}/indicators') {
      return Promise.resolve({ data: { ticker: 'AAPL', indicators } });
    }
    if (path === '/api/v1/me/chart-preferences') return Promise.resolve({ data: preferences });
    throw new Error(`unstubbed GET ${path}`);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  apiPut.mockResolvedValue({ data: PREFERENCES });
});

/**
 * Rendered through a real route rather than by stubbing `useParams`: the page
 * reads the ticker from the URL and links back to the watchlists, so both need
 * a router. The lower-case entry also exercises the uppercasing.
 */
function renderPage() {
  return renderWithProviders(
    <MemoryRouter initialEntries={['/symbols/aapl']}>
      <Routes>
        <Route path="/symbols/:ticker" element={<SymbolDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('SymbolDetailPage', () => {
  it('names the symbol it is charting', async () => {
    stubGet();
    renderPage();

    expect(await screen.findByRole('heading', { name: 'AAPL' })).toBeInTheDocument();
    expect(screen.getByText('Apple Inc.')).toBeInTheDocument();
  });

  it('uppercases the ticker from the URL', async () => {
    // The route param is `aapl`; the API is keyed on AAPL.
    stubGet();
    renderPage();

    await screen.findByTestId('chart');
    expect(apiGet).toHaveBeenCalledWith('/api/v1/symbols/{ticker}/bars', {
      params: { path: { ticker: 'AAPL' }, query: { range: '1Y' } },
    });
  });

  it('shows a spinner while the series load', () => {
    stubGet();
    renderPage();

    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  it('says what to do about a symbol with no history yet', async () => {
    // The common case: the universe is seeded but only a few symbols are
    // backfilled, so this is not a corner case to hide behind a blank chart.
    stubGet({ bars: [], indicators: [] });
    renderPage();

    expect(await screen.findByText(/No price history yet/)).toBeInTheDocument();
    expect(screen.getByText(/make backfill tickers=AAPL/)).toBeInTheDocument();
    expect(screen.queryByTestId('chart')).not.toBeInTheDocument();
  });

  it('explains an unknown ticker rather than showing an empty chart', async () => {
    stubGet({ symbolError: new ApiError(404, null, 'Unknown ticker') });
    renderPage();

    expect(await screen.findByText(/No symbol called AAPL/)).toBeInTheDocument();
  });

  it('offers a retry when the series fail to load', async () => {
    stubGet({ barsError: new ApiError(502, null, 'Upstream is down') });
    renderPage();

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  // ------------------------------------------------------------ preferences

  it('seeds the controls from the saved preferences', async () => {
    stubGet({
      preferences: { default_range: '3M', active_overlays: ['sma_200'], active_oscillators: [] },
    });
    renderPage();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: '3M' })).toHaveAttribute('aria-pressed', 'true'),
    );
    expect(screen.getByRole('button', { name: 'SMA 200' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('refetches the window when the range changes', async () => {
    stubGet();
    renderPage();
    await screen.findByTestId('chart');

    await userEvent.click(screen.getByRole('button', { name: '1M' }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith('/api/v1/symbols/{ticker}/bars', {
        params: { path: { ticker: 'AAPL' }, query: { range: '1M' } },
      }),
    );
  });

  it('saves the whole preference whenever a control changes', async () => {
    stubGet();
    renderPage();
    await screen.findByTestId('chart');

    await userEvent.click(screen.getByRole('button', { name: 'SMA 50' }));

    await waitFor(() =>
      expect(apiPut).toHaveBeenCalledWith('/api/v1/me/chart-preferences', {
        body: {
          default_range: '1Y',
          active_overlays: ['sma_20', 'sma_50'],
          active_oscillators: ['rsi_14'],
        },
      }),
    );
  });

  it('keeps the choice on screen when saving it fails, and says so', async () => {
    // A failed write must not silently discard what the user picked.
    stubGet();
    apiPut.mockRejectedValue(new ApiError(500, null, 'Nope'));
    renderPage();
    await screen.findByTestId('chart');

    await userEvent.click(screen.getByRole('button', { name: 'SMA 50' }));

    expect(await screen.findByText(/could not be saved/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SMA 50' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('chart')).toHaveAttribute('data-overlays', 'sma_20,sma_50');
  });

  // -------------------------------------------------------------- readout

  it('reads out the latest bar before anything is hovered', async () => {
    stubGet();
    renderPage();
    await screen.findByTestId('chart');

    expect(screen.getByText('latest')).toBeInTheDocument();
    expect(screen.getByText('215.00')).toBeInTheDocument();
  });
});
