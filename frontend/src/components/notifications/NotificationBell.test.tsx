/**
 * The bell, wired to the real realtime hook with a fake WebSocket.
 *
 * Two behaviours: a `notification_created` push moves the badge without a
 * reload, and polling is the fallback, off while the socket is up and back on
 * when it drops.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { session } from '@/lib/api/session';
import { queryKeys } from '@/lib/queryKeys';
import { useRealtime } from '@/lib/realtime/useRealtime';
import { renderWithProviders } from '@/test/utils';
import { NotificationBell } from './NotificationBell';

class FakeSocket {
  static OPEN = 1;
  static latest: FakeSocket | null = null;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  constructor() {
    FakeSocket.latest = this;
  }
  send() {}
  close() {}
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(event: unknown) {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent);
  }
  drop(code: number) {
    this.onclose?.({ code } as CloseEvent);
  }
}

let unread = 0;

beforeEach(() => {
  unread = 0;
  session.setAccessToken('token');
  vi.stubGlobal('WebSocket', FakeSocket);
  vi.stubGlobal(
    'fetch',
    vi.fn((input: Request) => {
      const body = input.url.endsWith('/unread-count')
        ? { count: unread }
        : { items: [], next_cursor: null };
      return Promise.resolve(
        new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } }),
      );
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  session.clear();
});

function Harness() {
  useRealtime(true);
  return <NotificationBell />;
}

function renderBell() {
  return renderWithProviders(
    <MemoryRouter>
      <Harness />
    </MemoryRouter>,
  );
}

function unreadPollInterval(client: ReturnType<typeof renderBell>['client']) {
  const query = client.getQueryCache().find({ queryKey: queryKeys.notifications.unreadCount() });
  return query?.observers[0]?.options.refetchInterval;
}

describe('NotificationBell', () => {
  it('updates its count from a push, without a reload', async () => {
    renderBell();
    expect(await screen.findByRole('button', { name: 'Notifications' })).toBeInTheDocument();

    act(() => {
      FakeSocket.latest!.open();
      FakeSocket.latest!.push({ type: 'ready' });
    });
    unread = 1;
    act(() => {
      FakeSocket.latest!.push({ type: 'notification_created', user_id: 'u', notification_id: 'n' });
    });

    expect(
      await screen.findByRole('button', { name: 'Notifications, 1 unread' }),
    ).toBeInTheDocument();
  });

  it('polls only while the socket is down', async () => {
    const { client } = renderBell();
    await screen.findByRole('button', { name: 'Notifications' });
    expect(unreadPollInterval(client)).toBe(60_000);

    act(() => {
      FakeSocket.latest!.open();
      FakeSocket.latest!.push({ type: 'ready' });
    });
    await waitFor(() => expect(unreadPollInterval(client)).toBe(false));

    act(() => FakeSocket.latest!.drop(1006));
    await waitFor(() => expect(unreadPollInterval(client)).toBe(60_000));
  });
});
