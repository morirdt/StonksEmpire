/**
 * The screener page.
 *
 * The conversion between builder rows and the wire format is tested directly in
 * `lib/filterTree.test.ts`; what is left for the DOM is the part only the DOM
 * can show — that the builder is generated from the catalogue rather than from
 * a list in TypeScript, that adding and removing filters works, and that the
 * three empty states really are three different sentences. That last one
 * matters most: "the universe is empty" and "nothing matched" look identical to
 * a user and have completely different fixes.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as ApiClientModule from '@/lib/api/client';
import { renderWithProviders } from '@/test/utils';
import { ScreenerPage } from './ScreenerPage';

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

const CATALOGUE = {
  fields: [
    { key: 'ticker', label: 'Ticker', unit: 'text', kinds: [], values: [] },
    { key: 'close', label: 'Close', unit: 'price', kinds: ['compare', 'numeric'], values: [] },
    { key: 'volume', label: 'Volume', unit: 'shares', kinds: ['compare', 'numeric'], values: [] },
    { key: 'sma_200', label: 'SMA 200', unit: 'price', kinds: ['compare', 'numeric'], values: [] },
    {
      key: 'change_percent_1d',
      label: 'Change % (1d)',
      unit: 'percent',
      kinds: ['numeric'],
      values: [],
    },
    {
      key: 'exchange',
      label: 'Exchange',
      unit: 'text',
      kinds: ['category'],
      values: ['NASDAQ', 'NYSE'],
    },
  ],
  sort_fields: ['ticker', 'close', 'volume', 'sma_200', 'change_percent_1d'],
  default_sort: { field: 'ticker', direction: 'asc' },
  max_limit: 200,
  max_depth: 3,
  max_nodes: 25,
};

const COLUMNS = [
  { key: 'close', label: 'Close', unit: 'price' },
  { key: 'volume', label: 'Volume', unit: 'shares' },
  { key: 'change_percent_1d', label: 'Change % (1d)', unit: 'percent' },
  { key: 'sma_200', label: 'SMA 200', unit: 'price' },
];

function runResponse(overrides: Record<string, unknown> = {}) {
  return {
    as_of: '2026-08-21',
    universe_size: 530,
    total_matched: 1,
    columns: COLUMNS,
    rows: [
      {
        ticker: 'MSFT',
        name: 'Microsoft Corporation',
        exchange: 'NASDAQ',
        asset_type: 'common_stock',
        values: {
          close: '412.500000',
          volume: '28000000',
          change_percent_1d: '1.250000',
          sma_200: '390.100000',
        },
      },
    ],
    ...overrides,
  };
}

function renderPage() {
  return renderWithProviders(
    <MemoryRouter>
      <ScreenerPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiGet.mockImplementation((path: string) => {
    if (path === '/api/v1/screener/fields') return Promise.resolve({ data: CATALOGUE });
    if (path === '/api/v1/screener/presets') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: undefined });
  });
  apiPost.mockResolvedValue({ data: runResponse() });
});

async function addValueFilter(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /add value filter/i }));
}

describe('the filter builder', () => {
  it('offers only the fields the catalogue says are screenable', async () => {
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);

    await user.click(screen.getByLabelText('Field'));
    const options = within(screen.getByRole('listbox')).getAllByRole('option');

    // `ticker` declares no filter kinds, so it must not be offered here even
    // though it is a registry entry and a sortable column.
    expect(options.map((o) => o.textContent)).toEqual([
      'Close',
      'Volume',
      'SMA 200',
      'Change % (1d)',
    ]);
  });

  it('adds, edits and removes a filter', async () => {
    const user = userEvent.setup();
    renderPage();

    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100');
    expect(screen.getByLabelText('Value')).toHaveValue('100');

    await user.click(screen.getByRole('button', { name: /remove this filter/i }));

    expect(screen.queryByLabelText('Value')).not.toBeInTheDocument();
    expect(screen.getByText(/no filters yet/i)).toBeInTheDocument();
  });

  it('shows a second input only for a between filter', async () => {
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);

    expect(screen.queryByLabelText('Upper value')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Operator'));
    await user.click(screen.getByRole('option', { name: 'is between' }));

    expect(screen.getByLabelText('Upper value')).toBeInTheDocument();
  });

  it('offers only same-unit fields on the right of a comparison', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /add field comparison/i }));

    await user.click(screen.getByLabelText('Right field'));
    const options = within(screen.getByRole('listbox')).getAllByRole('option');

    // A price against a share count is a 422; a dropdown that can produce one
    // is a dropdown that will.
    expect(options.map((o) => o.textContent)).toEqual(['Close', 'SMA 200']);
  });

  it('offers the category values the catalogue reports', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /add category filter/i }));

    await user.click(screen.getByLabelText('Values'));

    expect(
      within(screen.getByRole('listbox'))
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['NASDAQ', 'NYSE']);
  });

  it('cannot run until a filter is complete', async () => {
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);

    expect(screen.getByRole('button', { name: /run screen/i })).toBeDisabled();

    await user.type(screen.getByLabelText('Value'), '100');

    expect(screen.getByRole('button', { name: /run screen/i })).toBeEnabled();
  });
});

describe('running a screen', () => {
  it('sends the value as a string, never as a number', async () => {
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100.10');
    await user.click(screen.getByRole('button', { name: /run screen/i }));

    await waitFor(() => expect(apiPost).toHaveBeenCalled());
    const call = apiPost.mock.calls[0]![1] as {
      body: { filters: { children: { value: unknown }[] } };
    };
    expect(call.body.filters.children[0]!.value).toBe('100.10');
  });

  it('renders a column for every field the run referenced', async () => {
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100');
    await user.click(screen.getByRole('button', { name: /run screen/i }));

    expect(await screen.findByRole('columnheader', { name: 'SMA 200' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Change % (1d)' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'MSFT' })).toHaveAttribute('href', '/symbols/MSFT');
  });

  it('says how many matched against how many were shown', async () => {
    apiPost.mockResolvedValue({ data: runResponse({ total_matched: 412 }) });
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100');
    await user.click(screen.getByRole('button', { name: /run screen/i }));

    expect(await screen.findByText(/412 matches, showing 1/)).toBeInTheDocument();
    expect(screen.getByText(/530 symbols considered/)).toBeInTheDocument();
  });
});

describe('the three empty states', () => {
  it('says "no filters yet" before anything has been built', async () => {
    renderPage();

    expect(await screen.findByText(/no filters yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/no symbol matched/i)).not.toBeInTheDocument();
  });

  it('says "no symbol matched" when the universe is populated but nothing hit', async () => {
    apiPost.mockResolvedValue({ data: runResponse({ total_matched: 0, rows: [] }) });
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100');
    await user.click(screen.getByRole('button', { name: /run screen/i }));

    expect(await screen.findByText(/no symbol matched/i)).toBeInTheDocument();
    expect(screen.queryByText(/universe is empty/i)).not.toBeInTheDocument();
  });

  it('says the universe is empty when nothing has been backfilled', async () => {
    // The state that would otherwise be diagnosed as "nothing matched" — and
    // whose fix is a command, not a looser filter.
    apiPost.mockResolvedValue({
      data: runResponse({ as_of: null, universe_size: 0, total_matched: 0, rows: [] }),
    });
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100');
    await user.click(screen.getByRole('button', { name: /run screen/i }));

    expect(await screen.findByText(/universe is empty/i)).toBeInTheDocument();
    expect(screen.getByText(/make backfill/)).toBeInTheDocument();
    expect(screen.queryByText(/no symbol matched/i)).not.toBeInTheDocument();
  });
});

describe('presets', () => {
  it('saves the current screen under a name', async () => {
    const user = userEvent.setup();
    renderPage();
    await addValueFilter(user);
    await user.type(screen.getByLabelText('Value'), '100');

    await user.click(screen.getByRole('button', { name: /save as/i }));
    await user.type(screen.getByLabelText('Name'), 'My screen');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        '/api/v1/screener/presets',
        expect.objectContaining({
          body: expect.objectContaining({ name: 'My screen' }),
        }),
      ),
    );
  });

  it('re-sorting a saved screen re-runs it rather than re-ordering the capped rows', async () => {
    // The preset run endpoint takes no sort — the saved screen includes its
    // order — so a new sort has to go back through /screener/run.
    const preset = {
      id: 'preset-1',
      name: 'Above the 200',
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
      filters: {
        kind: 'group',
        op: 'and',
        children: [{ kind: 'compare', left: 'close', op: 'gt', right: 'sma_200' }],
      },
      sort: { field: 'ticker', direction: 'asc' },
    };
    apiGet.mockImplementation((path: string) => {
      if (path === '/api/v1/screener/fields') return Promise.resolve({ data: CATALOGUE });
      if (path === '/api/v1/screener/presets') return Promise.resolve({ data: [preset] });
      if (path === '/api/v1/screener/presets/{preset_id}') return Promise.resolve({ data: preset });
      return Promise.resolve({ data: undefined });
    });

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByLabelText('Saved screens'));
    await user.click(await screen.findByRole('option', { name: 'Above the 200' }));
    await user.click(await screen.findByRole('columnheader', { name: 'Close' }));

    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        '/api/v1/screener/run',
        expect.objectContaining({
          body: expect.objectContaining({ sort: { field: 'close', direction: 'asc' } }),
        }),
      ),
    );
  });

  it('runs a saved screen through the preset endpoint, not by reposting its tree', async () => {
    const preset = {
      id: 'preset-1',
      name: 'Above the 200',
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
      filters: {
        kind: 'group',
        op: 'and',
        children: [{ kind: 'compare', left: 'close', op: 'gt', right: 'sma_200' }],
      },
      sort: { field: 'close', direction: 'desc' },
    };
    apiGet.mockImplementation((path: string) => {
      if (path === '/api/v1/screener/fields') return Promise.resolve({ data: CATALOGUE });
      if (path === '/api/v1/screener/presets') return Promise.resolve({ data: [preset] });
      if (path === '/api/v1/screener/presets/{preset_id}') return Promise.resolve({ data: preset });
      return Promise.resolve({ data: undefined });
    });

    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByLabelText('Saved screens'));
    await user.click(await screen.findByRole('option', { name: 'Above the 200' }));

    // The builder shows what was stored...
    expect(await screen.findByLabelText('Left field')).toBeInTheDocument();
    // ...and the run goes through /presets/{id}/run, so what runs is what is
    // saved under that name rather than whatever the client rebuilt.
    await waitFor(() =>
      expect(apiPost).toHaveBeenCalledWith(
        '/api/v1/screener/presets/{preset_id}/run',
        expect.objectContaining({ params: { path: { preset_id: 'preset-1' } } }),
      ),
    );
  });
});
