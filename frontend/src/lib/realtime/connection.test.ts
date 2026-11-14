/**
 * The realtime socket: auth, invalidation, reconnects, and the shared refresh.
 *
 * A fake WebSocket stands in for the browser's; the server's side of the
 * protocol is tested in `backend/tests/integration/test_websocket.py`. The
 * property that matters most is the last one: **a 4401 joins the in-flight
 * refresh rather than starting its own**, because refresh tokens rotate and a
 * second refresh would get the session family revoked as theft.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import { api, refreshAccessToken } from '@/lib/api/client';
import { session } from '@/lib/api/session';
import { queryKeys } from '@/lib/queryKeys';
import { CLOSE_UNAUTHENTICATED, RealtimeConnection, quoteKeysToInvalidate } from './connection';

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
  }

  // ---- driven by the test, as the server would
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  push(event: unknown) {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent);
  }
  drop(code: number) {
    this.readyState = 3;
    this.onclose?.({ code } as CloseEvent);
  }
}

function grid(tickers: string[]) {
  return {
    watchlist: { id: 'w' },
    rows: tickers.map((ticker) => ({ item: { id: ticker }, symbol: { ticker }, quote: null })),
  };
}

let queryClient: QueryClient;
let scheduled: { callback: () => void; ms: number }[];
let connections: RealtimeConnection[] = [];

function connect(refresh: () => Promise<string | null> = () => Promise.resolve(null)) {
  const connection = new RealtimeConnection({
    url: 'ws://test/api/v1/ws',
    queryClient,
    refresh,
    createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    random: () => 1,
    schedule: (callback, ms) => scheduled.push({ callback, ms }),
    cancel: () => undefined,
  });
  connection.start();
  connections.push(connection);
  return connection;
}

function latest(): FakeSocket {
  return FakeSocket.instances.at(-1)!;
}

beforeEach(() => {
  FakeSocket.instances = [];
  scheduled = [];
  queryClient = new QueryClient();
  session.setAccessToken('token-1');
  // jsdom has no WebSocket constant table on the fake; the module reads OPEN.
  vi.stubGlobal('WebSocket', { OPEN: 1 });
});

afterEach(() => {
  // A connection left running stays subscribed to session changes, and would
  // open sockets of its own when a later test changes the token.
  for (const connection of connections) connection.stop();
  connections = [];
  vi.unstubAllGlobals();
  session.clear();
  session.onUnauthenticated(null);
});

describe('the handshake', () => {
  it('authenticates with its first message and is open only once the server says ready', () => {
    const connection = connect();
    latest().open();

    expect(latest().sent).toEqual([{ type: 'auth', token: 'token-1' }]);
    expect(connection.getStatus()).toBe('connecting');

    latest().push({ type: 'ready' });
    expect(connection.getStatus()).toBe('open');
  });

  it('re-authenticates on the same socket whenever the token changes', () => {
    connect();
    latest().open();
    latest().push({ type: 'ready' });

    session.setAccessToken('token-2');

    expect(FakeSocket.instances).toHaveLength(1);
    expect(latest().sent.at(-1)).toEqual({ type: 'auth', token: 'token-2' });
  });

  it('does not connect without a token', () => {
    session.clear();
    connect();
    expect(FakeSocket.instances).toHaveLength(0);
  });
});

describe('invalidation', () => {
  it('refetches only the grids showing an updated ticker, and nothing else', () => {
    queryClient.setQueryData(queryKeys.watchlists.grid('tech'), grid(['AAPL', 'MSFT']));
    queryClient.setQueryData(queryKeys.watchlists.grid('etfs'), grid(['SPY']));
    queryClient.setQueryData(queryKeys.watchlists.list(), []);
    queryClient.setQueryData(queryKeys.notifications.unreadCount(), 0);

    const keys = quoteKeysToInvalidate(queryClient, ['AAPL']);

    expect(keys).toEqual([queryKeys.watchlists.grid('tech')]);
  });

  it('marks exactly those queries stale on a quotes_updated push', () => {
    queryClient.setQueryData(queryKeys.watchlists.grid('tech'), grid(['AAPL']));
    queryClient.setQueryData(queryKeys.watchlists.grid('etfs'), grid(['SPY']));
    queryClient.setQueryData(queryKeys.notifications.unreadCount(), 0);
    connect();
    latest().open();

    latest().push({ type: 'quotes_updated', fetched_at: 'now', tickers: ['AAPL'] });

    const stale = (key: readonly unknown[]) => queryClient.getQueryState(key)?.isInvalidated;
    expect(stale(queryKeys.watchlists.grid('tech'))).toBe(true);
    expect(stale(queryKeys.watchlists.grid('etfs'))).toBe(false);
    expect(stale(queryKeys.notifications.unreadCount())).toBe(false);
  });

  it('marks notifications and alerts stale on notification_created, and never writes data', () => {
    queryClient.setQueryData(queryKeys.notifications.unreadCount(), 3);
    queryClient.setQueryData(queryKeys.alerts.list(), []);
    queryClient.setQueryData(queryKeys.watchlists.grid('tech'), grid(['AAPL']));
    connect();
    latest().open();

    latest().push({ type: 'notification_created', user_id: 'u', notification_id: 'n' });

    expect(queryClient.getQueryState(queryKeys.notifications.unreadCount())?.isInvalidated).toBe(
      true,
    );
    expect(queryClient.getQueryState(queryKeys.alerts.list())?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(queryKeys.watchlists.grid('tech'))?.isInvalidated).toBe(false);
    expect(queryClient.getQueryData(queryKeys.notifications.unreadCount())).toBe(3);
  });
});

describe('reconnecting', () => {
  it('backs off with jitter after a drop, and resets once ready again', () => {
    const connection = connect();
    latest().open();
    latest().drop(1006);
    expect(connection.getStatus()).toBe('closed');
    expect(scheduled.at(-1)!.ms).toBe(1_000);

    scheduled.at(-1)!.callback();
    latest().drop(1006);
    expect(scheduled.at(-1)!.ms).toBe(2_000);

    scheduled.at(-1)!.callback();
    latest().open();
    latest().push({ type: 'ready' });
    latest().drop(1013);
    expect(scheduled.at(-1)!.ms).toBe(1_000);
  });

  it('on 4401 refreshes, then reconnects with the new token', async () => {
    const refresh = vi.fn(() => {
      session.setAccessToken('token-2');
      return Promise.resolve('token-2');
    });
    connect(refresh);
    latest().open();

    latest().drop(CLOSE_UNAUTHENTICATED);
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(2));

    expect(refresh).toHaveBeenCalledTimes(1);
    latest().open();
    expect(latest().sent).toEqual([{ type: 'auth', token: 'token-2' }]);
  });

  it('on 4401 with no session left, signs out instead of looping', async () => {
    const signedOut = vi.fn();
    session.onUnauthenticated(signedOut);
    connect(() => Promise.resolve(null));
    latest().open();

    latest().drop(CLOSE_UNAUTHENTICATED);

    await vi.waitFor(() => expect(signedOut).toHaveBeenCalled());
    expect(FakeSocket.instances).toHaveLength(1);
    expect(scheduled).toEqual([]);
  });
});

describe('the shared refresh', () => {
  it('two simultaneous 401s plus a 4401 produce exactly one refresh call', async () => {
    const REFRESH_URL = 'http://localhost:8000/api/v1/auth/refresh';
    let releaseRefresh!: () => void;
    const refreshGate = new Promise<void>((resolve) => (releaseRefresh = resolve));

    const fetchMock = vi.fn(async (input: Request | string) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url === REFRESH_URL) {
        await refreshGate;
        return new Response(JSON.stringify({ access_token: 'token-2' }), { status: 200 });
      }
      const auth = (input as Request).headers.get('Authorization');
      return auth === 'Bearer token-2'
        ? new Response('{"count":0}', {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        : new Response('{}', { status: 401 });
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('WebSocket', { OPEN: 1 });

    connect(refreshAccessToken);
    latest().open();

    const first = api.GET('/api/v1/me/notifications/unread-count');
    const second = api.GET('/api/v1/me/notifications/unread-count');
    // Let both requests reach their 401 and start waiting on the refresh.
    await vi.waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3));
    latest().drop(CLOSE_UNAUTHENTICATED);
    releaseRefresh();
    await Promise.all([first, second]);

    const refreshes = fetchMock.mock.calls.filter(([input]) => {
      const url = typeof input === 'string' ? input : input.url;
      return url === REFRESH_URL;
    });
    expect(refreshes).toHaveLength(1);
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(2));
  });
});
