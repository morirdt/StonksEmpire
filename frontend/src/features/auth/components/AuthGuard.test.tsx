import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { renderWithProviders } from '@/test/utils';
import { AuthGuard } from './AuthGuard';
import { useAuthStore } from '../store/authStore';

function renderAt(initialEntry: string) {
  const router = createMemoryRouter(
    [
      {
        path: '/',
        children: [
          { path: 'login', element: <p>Sign in page</p> },
          { element: <AuthGuard />, children: [{ index: true, element: <p>Secret dashboard</p> }] },
        ],
      },
    ],
    { initialEntries: [initialEntry] },
  );
  return renderWithProviders(<RouterProvider router={router} />);
}

describe('AuthGuard', () => {
  it('waits rather than redirecting while the session is still unknown', async () => {
    // The bug this prevents: a reload bouncing a signed-in user to /login
    // because the boot-time refresh had not answered yet.
    useAuthStore.setState({ status: 'unknown', user: null });

    renderAt('/');

    expect(await screen.findByLabelText('Restoring your session')).toBeInTheDocument();
    expect(screen.queryByText('Sign in page')).not.toBeInTheDocument();
    expect(screen.queryByText('Secret dashboard')).not.toBeInTheDocument();
  });

  it('redirects an anonymous visitor to the login page', async () => {
    useAuthStore.setState({ status: 'anonymous', user: null });

    renderAt('/');

    expect(await screen.findByText('Sign in page')).toBeInTheDocument();
  });

  it('renders the protected page for a signed-in user', async () => {
    useAuthStore.setState({
      status: 'authenticated',
      user: {
        id: 'u-1',
        email: 'a@example.com',
        display_name: null,
        is_active: true,
        created_at: '2026-01-01T00:00:00Z',
      },
    });

    renderAt('/');

    expect(await screen.findByText('Secret dashboard')).toBeInTheDocument();
  });
});
