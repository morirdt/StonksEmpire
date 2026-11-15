/**
 * One WebSocket per tab, turning server pushes into query invalidations.
 *
 * **A push is a hint, not data.** Every message says *that* something changed;
 * this module invalidates the matching TanStack Query keys and the normal REST
 * path refetches. It never writes into the cache, so the grid cannot disagree
 * with the endpoint, and prices stay strings parsed only in `lib/format.ts`.
 *
 * The protocol (see `backend/app/api/v1/routes/ws.py`):
 *
 * - Authenticate by first message, `{type: 'auth', token}`. The server answers
 *   `{type: 'ready'}`, and only then is the socket "open" for polling purposes.
 * - **Re-authenticate on every token change**, through `session.onChange`. The
 *   server closes a socket whose token expires, so a refresh that is not
 *   forwarded here costs a reconnect.
 * - **On 4401, go through the one shared refresh** in `lib/api/client.ts` and
 *   reconnect. Never refresh here: refresh tokens rotate, and a second refresh
 *   racing a request's would spend an already-spent token, which the backend
 *   treats as theft and answers by revoking the whole session.
 * - Anything else (a network drop, a 1013 because the server's Redis is down)
 *   reconnects with exponential backoff and full jitter. While the socket is
 *   not open, consumers poll instead (see `useRealtimeConnected`).
 */
import type { QueryClient, QueryKey } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import { session } from '@/lib/api/session';
import type { components } from '@/lib/api/schema';

export type RealtimeStatus = 'idle' | 'connecting' | 'open' | 'closed';

type WatchlistGrid = components['schemas']['WatchlistDetailResponse'];

export type RealtimeEvent =
  | { type: 'ready' }
  | { type: 'quotes_updated'; fetched_at: string; tickers: string[] }
  | { type: 'notification_created'; user_id: string; notification_id: string };

/** Close codes the server uses. 4401 mirrors HTTP 401. */
export const CLOSE_UNAUTHENTICATED = 4401;

const BACKOFF_BASE_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;

export interface RealtimeOptions {
  url: string;
  queryClient: QueryClient;
  /** The shared single-flight refresh. Resolves to the new token or null. */
  refresh: () => Promise<string | null>;
  createSocket?: (url: string) => WebSocket;
  random?: () => number;
  schedule?: (callback: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
}

/**
 * Which cached queries a `quotes_updated` should refetch: those showing one of
 * its tickers, and nothing else.
 *
 * Exported and pure so the "right keys and nothing else" rule is tested
 * directly. It reads the cache rather than a registry of what is on screen:
 * a grid's rows *are* the record of which tickers it shows.
 */
export function quoteKeysToInvalidate(queryClient: QueryClient, tickers: string[]): QueryKey[] {
  const updated = new Set(tickers);
  const keys: QueryKey[] = [];

  for (const query of queryClient.getQueryCache().findAll({ queryKey: ['watchlists', 'grid'] })) {
    const data = query.state.data as WatchlistGrid | undefined;
    if (data?.rows.some((row) => updated.has(row.symbol.ticker))) keys.push(query.queryKey);
  }
  for (const query of queryClient.getQueryCache().findAll({ queryKey: queryKeys.quotes.all() })) {
    const joined = query.queryKey[1];
    if (typeof joined === 'string' && joined.split(',').some((t) => updated.has(t))) {
      keys.push(query.queryKey);
    }
  }
  return keys;
}

export class RealtimeConnection {
  private socket: WebSocket | null = null;
  private status: RealtimeStatus = 'idle';
  private running = false;
  private attempt = 0;
  private reconnectHandle: unknown = null;
  private unsubscribeSession: (() => void) | null = null;
  private readonly listeners = new Set<(status: RealtimeStatus) => void>();
  private readonly options: Required<RealtimeOptions>;

  constructor(options: RealtimeOptions) {
    this.options = {
      createSocket: (url) => new WebSocket(url),
      random: Math.random,
      schedule: (callback, ms) => setTimeout(callback, ms),
      cancel: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
      ...options,
    };
  }

  getStatus(): RealtimeStatus {
    return this.status;
  }

  subscribe(listener: (status: RealtimeStatus) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.unsubscribeSession = session.onChange((token) => this.onTokenChange(token));
    this.connect();
  }

  stop(): void {
    this.running = false;
    this.unsubscribeSession?.();
    this.unsubscribeSession = null;
    if (this.reconnectHandle !== null) this.options.cancel(this.reconnectHandle);
    this.reconnectHandle = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close(1000);
    this.setStatus('idle');
  }

  private setStatus(next: RealtimeStatus) {
    if (next === this.status) return;
    this.status = next;
    for (const listener of this.listeners) listener(next);
  }

  private connect() {
    // A token change can arrive while a backoff reconnect is pending; the
    // pending one must not open a second socket after this one.
    if (this.reconnectHandle !== null) this.options.cancel(this.reconnectHandle);
    this.reconnectHandle = null;
    if (!this.running || this.socket || !session.getAccessToken()) return;
    this.setStatus('connecting');
    const socket = this.options.createSocket(this.options.url);
    this.socket = socket;

    socket.onopen = () => this.sendAuth(session.getAccessToken());
    socket.onmessage = (message: MessageEvent) => this.onMessage(message);
    socket.onclose = (event: CloseEvent) => {
      if (this.socket !== socket) return; // superseded or stopped
      this.socket = null;
      this.setStatus('closed');
      void this.onClose(event.code);
    };
  }

  private sendAuth(token: string | null) {
    if (token && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'auth', token }));
    }
  }

  private onTokenChange(token: string | null) {
    if (token === null) {
      // Signed out: nothing to authenticate with, and nothing to listen for.
      this.socket?.close(1000);
      return;
    }
    if (this.socket) this.sendAuth(token);
    else if (this.status !== 'connecting') this.connect();
  }

  private onMessage(message: MessageEvent) {
    let event: RealtimeEvent;
    try {
      event = JSON.parse(String(message.data)) as RealtimeEvent;
    } catch {
      return;
    }
    const { queryClient } = this.options;

    switch (event.type) {
      case 'ready':
        this.attempt = 0;
        this.setStatus('open');
        break;
      case 'quotes_updated':
        for (const queryKey of quoteKeysToInvalidate(queryClient, event.tickers)) {
          void queryClient.invalidateQueries({ queryKey, exact: true });
        }
        break;
      case 'notification_created':
        void queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all() });
        // A firing also moves the alert's last_triggered_at and maybe its status.
        void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
        break;
    }
  }

  private async onClose(code: number) {
    if (!this.running) return;

    if (code === CLOSE_UNAUTHENTICATED) {
      const token = await this.options.refresh();
      if (!this.running) return;
      if (token === null) {
        // The same outcome as a request whose refresh failed: signed out.
        session.clear();
        session.notifyUnauthenticated();
        return;
      }
      // `refresh` set the new token, so onTokenChange may already have
      // reconnected. Only connect if it did not.
      if (!this.socket) this.connect();
      return;
    }

    const ceiling = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** this.attempt);
    this.attempt += 1;
    // Full jitter: after a server restart, every tab must not reconnect at once.
    const delay = Math.round(this.options.random() * ceiling);
    this.reconnectHandle = this.options.schedule(() => {
      this.reconnectHandle = null;
      this.connect();
    }, delay);
  }
}
