/**
 * The alerts page: the list's "last checked" column, and creating an alert from
 * a link that names a ticker, which is how the symbol page reaches it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { session } from '@/lib/api/session';
import { renderWithProviders } from '@/test/utils';
import { AlertsPage } from './AlertsPage';

const priceField = {
  key: 'price',
  label: 'Price',
  unit: 'price',
  kinds: ['compare', 'numeric'],
  values: [],
};
const smaField = { ...priceField, key: 'sma_200', label: 'SMA 200' };

const existing = {
  id: 'a1',
  name: 'AAPL breakout',
  kind: 'intraday',
  ticker: 'AAPL',
  preset_id: null,
  preset_name: null,
  condition_summary: 'Price > 200',
  trigger_mode: 'once',
  cooldown_minutes: 60,
  status: 'active',
  disabled_reason: null,
  last_state: false,
  last_evaluated_at: null,
  last_triggered_at: null,
  created_at: '2026-11-18T15:00:00Z',
  updated_at: '2026-11-18T15:00:00Z',
};

function json(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }),
  );
}

let posted: unknown[] = [];

beforeEach(() => {
  posted = [];
  session.setAccessToken('token');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (url.pathname === '/api/v1/alerts' && input.method === 'POST') {
        const body = (await input.json()) as Record<string, unknown>;
        posted.push(body);
        return json(
          { ...existing, ...body, id: 'a2', condition: body.condition, condition_met_now: true },
          201,
        );
      }
      if (url.pathname === '/api/v1/alerts') return json([existing]);
      if (url.pathname === '/api/v1/alerts/fields') {
        return json({
          fields: [priceField, smaField],
          sort_fields: [],
          default_sort: { field: 'ticker', direction: 'asc' },
          max_limit: 200,
          max_depth: 3,
          max_nodes: 25,
        });
      }
      return json({}, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  session.clear();
});

function renderAt(path: string) {
  return renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/alerts/new" element={<AlertsPage creating />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AlertsPage', () => {
  it('says an alert has not been checked yet rather than leaving it blank', async () => {
    renderAt('/alerts');

    const row = (await screen.findByText('AAPL breakout')).closest('tr')!;
    expect(within(row).getByText('Not yet')).toBeInTheDocument();
    expect(within(row).getByText('Price > 200')).toBeInTheDocument();
  });

  it('creates an intraday alert for the linked ticker and says when it is already true', async () => {
    const user = userEvent.setup();
    renderAt('/alerts/new?ticker=AAPL');

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Symbol AAPL')).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Name'), 'Above the 200-day');
    await user.click(await within(dialog).findByRole('button', { name: /add field comparison/i }));
    await user.click(within(dialog).getByRole('button', { name: 'Create alert' }));

    expect(posted).toEqual([
      {
        name: 'Above the 200-day',
        kind: 'intraday',
        ticker: 'AAPL',
        condition: {
          kind: 'group',
          op: 'and',
          children: [{ kind: 'compare', left: 'price', op: 'gt', right: 'sma_200' }],
        },
        trigger_mode: 'once',
      },
    ]);
    expect(await screen.findByText(/is already true right now/)).toBeInTheDocument();
  });
});
