import { useEffect } from 'react';
import { API_BASE_URL } from '@/lib/api/client';
import { session } from '@/lib/api/session';
import { useAuthStore } from '../store/authStore';
import { fetchCurrentUser } from './useAuth';

/**
 * One silent refresh at boot.
 *
 * The access token lives in memory only, so a reload always starts without
 * one. The HttpOnly refresh cookie survives, so exchanging it here is what
 * makes "still signed in after F5" true. Runs exactly once; until it settles
 * the store reports `unknown` and the app renders a splash rather than
 * bouncing the user to /login.
 */
export function useAuthBootstrap() {
  const status = useAuthStore((state) => state.status);

  useEffect(() => {
    let cancelled = false;

    async function restore() {
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
          method: 'POST',
          credentials: 'include',
        });
        if (!response.ok) throw new Error('no session');

        const body = (await response.json()) as { access_token: string };
        session.setAccessToken(body.access_token);

        const user = await fetchCurrentUser();
        if (!cancelled) useAuthStore.getState().signIn(user, body.access_token);
      } catch {
        if (!cancelled) useAuthStore.getState().markAnonymous();
      }
    }

    void restore();
    return () => {
      cancelled = true;
    };
    // Deliberately once: re-running would rotate the refresh token needlessly.
  }, []);

  return status;
}
