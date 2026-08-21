import { create } from 'zustand';
import { session } from '@/lib/api/session';
import type { components } from '@/lib/api/schema';

export type AuthUser = components['schemas']['UserResponse'];

/**
 * `unknown` is the state before the boot-time silent refresh has answered.
 * Without it, a reload renders the login page for a split second before the
 * refresh lands — the single most visible auth bug there is.
 */
export type AuthStatus = 'unknown' | 'authenticated' | 'anonymous';

interface AuthState {
  user: AuthUser | null;
  status: AuthStatus;
  /** Record a successful sign-in. The token is held outside React, in `session`. */
  signIn: (user: AuthUser, accessToken: string) => void;
  /** Record the user we resolved from an existing session. */
  setUser: (user: AuthUser) => void;
  signOut: () => void;
  markAnonymous: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'unknown',

  signIn: (user, accessToken) => {
    session.setAccessToken(accessToken);
    set({ user, status: 'authenticated' });
  },

  setUser: (user) => set({ user, status: 'authenticated' }),

  signOut: () => {
    session.clear();
    set({ user: null, status: 'anonymous' });
  },

  markAnonymous: () => set({ user: null, status: 'anonymous' }),
}));

/**
 * Lets the fetch layer drop the session when a refresh fails, without `lib`
 * importing this feature. Called once, at module load of the auth feature.
 */
session.onUnauthenticated(() => {
  useAuthStore.setState({ user: null, status: 'anonymous' });
});
