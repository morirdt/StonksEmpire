import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type * as ApiClientModule from '@/lib/api/client';
import { ApiError } from '@/lib/api/client';
import { renderWithProviders } from '@/test/utils';
import { DashboardPage } from './DashboardPage';

type MetaResponse = { name: string; version: string; environment: string; api_version: string };

// vi.mock factories are hoisted above the imports, so the spy has to be
// hoisted with them or it is still in the temporal dead zone when they run.
const { getMeta } = vi.hoisted(() => ({
  getMeta: vi.fn<(path: string) => Promise<{ data: MetaResponse }>>(),
}));

vi.mock('@/lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ApiClientModule>();
  return { ...actual, api: { GET: getMeta } };
});

describe('DashboardPage', () => {
  beforeEach(() => {
    getMeta.mockReset();
  });

  it('shows the API identity once the request resolves', async () => {
    getMeta.mockResolvedValue({
      data: { name: 'Stonks Empire', version: '0.1.0', environment: 'test', api_version: 'v1' },
    });

    renderWithProviders(<DashboardPage />);

    expect(await screen.findByText('v0.1.0')).toBeInTheDocument();
    expect(screen.getByText('connected')).toBeInTheDocument();
    expect(screen.getByText('api v1')).toBeInTheDocument();
  });

  it('surfaces the correlation id when the API fails', async () => {
    getMeta.mockRejectedValue(
      new ApiError(500, {
        type: 'https://stonks.empire/errors/internal_error',
        title: 'Internal Server Error',
        status: 500,
        detail: 'boom',
        instance: '/api/v1/meta',
        code: 'internal_error',
        correlation_id: 'corr-42',
      }),
    );

    renderWithProviders(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('corr-42');
    });
  });

  it('calls the versioned meta endpoint', async () => {
    getMeta.mockResolvedValue({
      data: { name: 'Stonks Empire', version: '0.1.0', environment: 'test', api_version: 'v1' },
    });

    renderWithProviders(<DashboardPage />);

    await waitFor(() => {
      expect(getMeta).toHaveBeenCalledWith('/api/v1/meta');
    });
  });
});
