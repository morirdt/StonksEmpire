import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as ApiClientModule from '@/lib/api/client';
import { ApiError } from '@/lib/api/client';
import { renderWithProviders } from '@/test/utils';
import { WatchlistsPage } from './WatchlistsPage';

/**
 * The grid links each ticker to its detail page, so the page needs a router
 * even though nothing here navigates. Without one every render throws on a
 * null router context.
 */
function renderPage() {
  return renderWithProviders(
    <MemoryRouter>
      <WatchlistsPage />
    </MemoryRouter>,
  );
}

const { apiGet, apiPost, apiPatch, apiDelete } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPatch: vi.fn(),
  apiDelete: vi.fn(),
}));

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ApiClientModule>();
  return {
    ...actual,
    api: { GET: apiGet, POST: apiPost, PATCH: apiPatch, DELETE: apiDelete },
  };
});

const WATCHLIST = {
  id: 'wl-1',
  name: 'Tech',
  is_default: false,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
  item_count: 2,
};

function row(ticker: string, name: string, price: string, change: string, position: number) {
  return {
    item: {
      id: `item-${ticker}`,
      symbol_id: `sym-${ticker}`,
      position,
      notes: null,
      created_at: '2026-08-01T00:00:00Z',
    },
    symbol: {
      id: `sym-${ticker}`,
      ticker,
      name,
      exchange: 'NASDAQ',
      asset_type: 'common_stock',
      currency: 'USD',
      is_active: true,
    },
    quote: {
      ticker,
      price,
      change,
      change_percent: change,
      day_open: price,
      day_high: price,
      day_low: price,
      previous_close: price,
      volume: 1_000_000,
      quoted_at: '2026-08-21T20:00:00Z',
      fetched_at: new Date().toISOString(),
    },
  };
}

/** Route GET by path so a test does not depend on call ordering. */
function stubGet(watchlists: unknown[], rows: unknown[]) {
  apiGet.mockImplementation((path: string) => {
    if (path === '/api/v1/watchlists') return Promise.resolve({ data: watchlists });
    if (path === '/api/v1/watchlists/{watchlist_id}/quotes') {
      return Promise.resolve({ data: { watchlist: WATCHLIST, rows } });
    }
    if (path === '/api/v1/symbols') return Promise.resolve({ data: { items: [] } });
    return Promise.resolve({ data: null });
  });
}

describe('WatchlistsPage', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
    apiPatch.mockReset();
    apiDelete.mockReset();
  });

  it('tells a new user what to do instead of showing an empty table', async () => {
    stubGet([], []);

    renderPage();

    expect(await screen.findByText('No watchlists yet')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /create your first watchlist/i }),
    ).toBeInTheDocument();
  });

  it('renders a row per symbol with its price', async () => {
    stubGet([WATCHLIST], [row('AAPL', 'Apple Inc.', '123.40', '1.50', 0)]);

    renderPage();

    expect(await screen.findByText('AAPL')).toBeInTheDocument();
    expect(screen.getByText('Apple Inc.')).toBeInTheDocument();
    expect(screen.getByText('123.40')).toBeInTheDocument();
  });

  it('signs gains and losses so direction is readable without colour', async () => {
    // Colour alone would fail anyone with a red-green deficiency; the sign is
    // what actually carries the meaning.
    stubGet(
      [WATCHLIST],
      [
        row('AAPL', 'Apple Inc.', '123.40', '1.50', 0),
        row('MSFT', 'Microsoft Corporation', '99.00', '-2.25', 1),
      ],
    );

    renderPage();

    expect(await screen.findByText('+1.50')).toBeInTheDocument();
    expect(screen.getByText('-2.25')).toBeInTheDocument();
    expect(screen.getByText('+1.50%')).toBeInTheDocument();
  });

  it('offers a retry when the list cannot be loaded', async () => {
    apiGet.mockRejectedValue(
      new ApiError(500, {
        type: 'about:blank',
        title: 'Internal Server Error',
        status: 500,
        detail: 'boom',
        instance: '/api/v1/watchlists',
        code: 'internal_error',
        correlation_id: 'corr-1',
      }),
    );

    renderPage();

    expect(await screen.findByText('boom')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('cannot move the first row up or the last row down', async () => {
    stubGet(
      [WATCHLIST],
      [
        row('AAPL', 'Apple Inc.', '123.40', '1.50', 0),
        row('MSFT', 'Microsoft Corporation', '99.00', '-2.25', 1),
      ],
    );

    renderPage();

    expect(await screen.findByRole('button', { name: 'Move AAPL up' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Move MSFT down' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Move AAPL down' })).toBeEnabled();
  });

  it('sends the complete new order when a row moves', async () => {
    stubGet(
      [WATCHLIST],
      [
        row('AAPL', 'Apple Inc.', '123.40', '1.50', 0),
        row('MSFT', 'Microsoft Corporation', '99.00', '-2.25', 1),
      ],
    );
    apiPatch.mockResolvedValue({ data: [] });

    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Move AAPL down' }));

    await waitFor(() => expect(apiPatch).toHaveBeenCalled());
    const [path, options] = apiPatch.mock.calls[0] as [string, { body: { item_ids: string[] } }];
    expect(path).toBe('/api/v1/watchlists/{watchlist_id}/items');
    // The whole ordering, not just the moved pair — a partial list is a 422.
    expect(options.body.item_ids).toEqual(['item-MSFT', 'item-AAPL']);
  });

  it('removes a symbol through the API', async () => {
    stubGet([WATCHLIST], [row('AAPL', 'Apple Inc.', '123.40', '1.50', 0)]);
    apiDelete.mockResolvedValue({ data: null });

    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Remove AAPL' }));

    await waitFor(() => expect(apiDelete).toHaveBeenCalled());
    const [path, options] = apiDelete.mock.calls[0] as [
      string,
      { params: { path: Record<string, string> } },
    ];
    expect(path).toBe('/api/v1/watchlists/{watchlist_id}/items/{item_id}');
    expect(options.params.path).toEqual({ watchlist_id: 'wl-1', item_id: 'item-AAPL' });
  });

  it('shows an empty grid with guidance when the list has no symbols', async () => {
    stubGet([{ ...WATCHLIST, item_count: 0 }], []);

    renderPage();

    expect(await screen.findByText(/no symbols yet/i)).toBeInTheDocument();
  });
});
