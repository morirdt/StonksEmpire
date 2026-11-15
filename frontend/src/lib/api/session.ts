/**
 * Where the access token lives: a module variable, and nothing else.
 *
 * Not `localStorage`, not a cookie, not React state. Anything readable from
 * JavaScript that survives a reload is stealable by XSS, and a token in React
 * state is unreachable from the fetch layer. A reload deliberately loses the
 * token — the silent refresh on boot gets a new one from the HttpOnly cookie.
 *
 * This sits in `lib/` rather than the auth feature so that `client.ts` can read
 * it without `lib` importing a feature.
 */

let accessToken: string | null = null;
let onUnauthenticated: (() => void) | null = null;
const listeners = new Set<(token: string | null) => void>();

function notify() {
  for (const listener of listeners) listener(accessToken);
}

export const session = {
  getAccessToken: (): string | null => accessToken,

  setAccessToken(token: string | null) {
    if (token === accessToken) return;
    accessToken = token;
    notify();
  },

  clear() {
    if (accessToken === null) return;
    accessToken = null;
    notify();
  },

  /**
   * Called with the new token whenever it changes, including to `null`.
   *
   * The realtime socket uses this to re-authenticate after every refresh, so a
   * socket never outlives the token it was opened with. Returns the unsubscribe.
   */
  onChange(listener: (token: string | null) => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  /**
   * Registered by the auth store so the fetch layer can report a refresh that
   * failed, without importing the store or the router.
   */
  onUnauthenticated(handler: (() => void) | null) {
    onUnauthenticated = handler;
  },

  notifyUnauthenticated() {
    onUnauthenticated?.();
  },
};
