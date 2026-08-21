import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { session } from '@/lib/api/session';
import { renderWithProviders } from '@/test/utils';
import { LoginPage } from './LoginPage';
import { useAuthStore } from '../store/authStore';

const user = {
  id: 'u-1',
  email: 'trader@example.com',
  display_name: 'Trader',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': status >= 400 ? 'application/problem+json' : 'application/json' },
  });
}

function renderLogin() {
  const router = createMemoryRouter(
    [
      { path: '/login', element: <LoginPage /> },
      { path: '/', element: <p>Dashboard</p> },
    ],
    { initialEntries: ['/login'] },
  );
  return renderWithProviders(<RouterProvider router={router} />);
}

beforeEach(() => {
  useAuthStore.setState({ status: 'anonymous', user: null });
  session.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LoginPage', () => {
  it('signs the user in and lands them on the dashboard', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: Request | string) => {
        const url = typeof input === 'string' ? input : input.url;
        if (url.endsWith('/login'))
          return Promise.resolve(json({ access_token: 'tok', expires_in: 900 }));
        if (url.endsWith('/me')) return Promise.resolve(json(user));
        return Promise.resolve(json({}, 404));
      }),
    );

    renderLogin();
    await userEvent.type(screen.getByLabelText(/email/i), 'trader@example.com');
    await userEvent.type(screen.getByLabelText(/password/i), 'a-long-enough-password');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Dashboard')).toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(session.getAccessToken()).toBe('tok');
  });

  it('shows the server message when the credentials are wrong', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(
          json(
            {
              type: 'https://stonks.empire/errors/authentication_failed',
              title: 'Authentication Failed',
              status: 401,
              detail: 'Invalid email or password.',
              instance: '/api/v1/auth/login',
              code: 'authentication_failed',
              correlation_id: 'corr-9',
            },
            401,
          ),
        ),
      ),
    );

    renderLogin();
    await userEvent.type(screen.getByLabelText(/email/i), 'trader@example.com');
    await userEvent.type(screen.getByLabelText(/password/i), 'the-wrong-password');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Invalid email or password.');
    expect(alert).toHaveTextContent('corr-9');
    expect(useAuthStore.getState().status).toBe('anonymous');
  });

  it('validates the email before calling the API', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    renderLogin();
    await userEvent.type(screen.getByLabelText(/email/i), 'not-an-email');
    await userEvent.type(screen.getByLabelText(/password/i), 'whatever-goes-here');
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });
});
