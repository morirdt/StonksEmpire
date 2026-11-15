/**
 * Desktop alerts, driven through the bell with a stubbed `Notification`.
 *
 * The count is moved by invalidating it, which is what both a push and the
 * polling fallback amount to; the bell test covers the socket itself.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { session } from '@/lib/api/session';
import { queryKeys } from '@/lib/queryKeys';
import { renderWithProviders } from '@/test/utils';
import { NotificationBell } from './NotificationBell';

class FakeNotification {
  static permission: NotificationPermission = 'granted';
  static requestPermission = vi.fn(() => Promise.resolve(FakeNotification.permission));
  static shown: FakeNotification[] = [];
  onclick: (() => void) | null = null;
  title: string;
  options: NotificationOptions;
  constructor(title: string, options: NotificationOptions) {
    this.title = title;
    this.options = options;
    FakeNotification.shown.push(this);
  }
  close() {}
}

function note(id: string, title: string) {
  return {
    id,
    kind: 'alert_triggered',
    title,
    body: `${title} body`,
    payload: {},
    alert_id: `alert-${id}`,
    read_at: null,
    created_at: '2026-11-15T20:00:00Z',
  };
}

let unread: ReturnType<typeof note>[] = [];

beforeEach(() => {
  unread = [note('n1', 'Before the page loaded')];
  FakeNotification.permission = 'granted';
  FakeNotification.shown = [];
  FakeNotification.requestPermission.mockClear();
  localStorage.clear();
  session.setAccessToken('token');
  vi.stubGlobal('Notification', FakeNotification);
  // jsdom does not implement it; a click on a real notification focuses the tab.
  vi.spyOn(window, 'focus').mockImplementation(() => {});
  vi.stubGlobal(
    'fetch',
    vi.fn((input: Request) => {
      const body = input.url.includes('/unread-count')
        ? { count: unread.length }
        : { items: [...unread].reverse(), next_cursor: null };
      return Promise.resolve(
        new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } }),
      );
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  session.clear();
  localStorage.clear();
});

function Where() {
  return <div data-testid="path">{useLocation().pathname}</div>;
}

function renderBell() {
  return renderWithProviders(
    <MemoryRouter>
      <NotificationBell />
      <Routes>
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function arrive(client: ReturnType<typeof renderBell>['client'], ...notes: string[]) {
  unread = [...unread, ...notes.map((title, i) => note(`new-${title}-${i}`, title))];
  await act(() => client.invalidateQueries({ queryKey: queryKeys.notifications.unreadCount() }));
}

describe('desktop alerts', () => {
  it('shows only what arrives after the page loaded, oldest first, and opens the alert on click', async () => {
    localStorage.setItem('stonks-empire:desktop-notifications', 'on');
    const { client } = renderBell();
    await screen.findByRole('button', { name: /1 unread/ });
    // Let the first fetch record the existing notification.
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.length).toBeGreaterThanOrEqual(2));

    await arrive(client, 'AAPL: Price > 200', 'MSFT: Price > 400');

    await waitFor(() => expect(FakeNotification.shown).toHaveLength(2));
    expect(FakeNotification.shown.map((n) => n.title)).toEqual([
      'AAPL: Price > 200',
      'MSFT: Price > 400',
    ]);

    act(() => FakeNotification.shown[0]!.onclick?.());
    expect(screen.getByTestId('path')).toHaveTextContent('/alerts/alert-new-AAPL: Price > 200-0');
  });

  it('stays quiet while switched off', async () => {
    const { client } = renderBell();
    await screen.findByRole('button', { name: /1 unread/ });

    await arrive(client, 'AAPL: Price > 200');

    await screen.findByRole('button', { name: /2 unread/ });
    expect(FakeNotification.shown).toEqual([]);
  });

  it('asks for permission when switched on, and says when the browser blocks it', async () => {
    const user = userEvent.setup();
    FakeNotification.permission = 'default';
    renderBell();

    await user.click(await screen.findByRole('button', { name: /notifications/i }));
    FakeNotification.requestPermission.mockImplementationOnce(() => {
      FakeNotification.permission = 'denied';
      return Promise.resolve('denied');
    });
    await user.click(screen.getByRole('switch', { name: 'Desktop alerts' }));

    expect(FakeNotification.requestPermission).toHaveBeenCalledOnce();
    expect(await screen.findByText(/blocked by the browser/i)).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Desktop alerts' })).toBeDisabled();
    expect(localStorage.getItem('stonks-empire:desktop-notifications')).toBeNull();
  });
});
