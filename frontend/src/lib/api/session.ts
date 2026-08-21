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

export const session = {
  getAccessToken: (): string | null => accessToken,

  setAccessToken(token: string | null) {
    accessToken = token;
  },

  clear() {
    accessToken = null;
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
