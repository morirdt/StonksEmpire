import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as ApiClientModule from '@/lib/api/client';
import { renderWithProviders } from '@/test/utils';
import { SymbolSearch } from './SymbolSearch';

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ApiClientModule>();
  return { ...actual, api: { GET: apiGet } };
});

function symbol(ticker: string, name: string) {
  return {
    id: `sym-${ticker}`,
    ticker,
    name,
    exchange: 'NASDAQ',
    asset_type: 'common_stock',
    currency: 'USD',
    is_active: true,
  };
}

describe('SymbolSearch', () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiGet.mockResolvedValue({
      data: { items: [symbol('AAPL', 'Apple Inc.'), symbol('AAPU', 'Apple-adjacent Fund')] },
    });
  });

  it('prompts before anything is typed rather than saying "no results"', async () => {
    renderWithProviders(<SymbolSearch onSelect={vi.fn()} />);

    await userEvent.click(screen.getByLabelText(/add a symbol/i));

    expect(await screen.findByText(/start typing a ticker or name/i)).toBeInTheDocument();
  });

  it('shows matching symbols with their names', async () => {
    renderWithProviders(<SymbolSearch onSelect={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/add a symbol/i), 'AAP');

    expect(await screen.findByText('Apple Inc. · NASDAQ')).toBeInTheDocument();
  });

  it('preserves the server ranking instead of re-filtering on the client', async () => {
    // The backend puts an exact ticker match first; a client-side filter would
    // silently reorder that.
    renderWithProviders(<SymbolSearch onSelect={vi.fn()} />);

    await userEvent.type(screen.getByLabelText(/add a symbol/i), 'AAPL');

    const options = await screen.findAllByRole('option');
    expect(options[0]).toHaveTextContent('AAPL');
    expect(options).toHaveLength(2);
  });

  it('marks symbols already on the list as disabled rather than hiding them', async () => {
    // Vanishing from search reads as "not found", which is a worse answer than
    // "already there".
    renderWithProviders(<SymbolSearch onSelect={vi.fn()} excludeTickers={['AAPL']} />);

    await userEvent.type(screen.getByLabelText(/add a symbol/i), 'AAP');

    expect(await screen.findByText(/already added/i)).toBeInTheDocument();
  });

  it('reports the chosen symbol', async () => {
    const onSelect = vi.fn();
    renderWithProviders(<SymbolSearch onSelect={onSelect} />);

    const input = screen.getByLabelText(/add a symbol/i);
    await userEvent.type(input, 'AAPL');
    await screen.findAllByRole('option');
    await userEvent.keyboard('{ArrowDown}{Enter}');

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ ticker: 'AAPL' }));
  });
});
