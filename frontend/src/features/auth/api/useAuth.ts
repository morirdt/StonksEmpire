import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { session } from '@/lib/api/session';
import { useAuthStore } from '../store/authStore';

async function fetchCurrentUser() {
  const { data } = await api.GET('/api/v1/auth/me');
  return data!;
}

export function useLogin() {
  const signIn = useAuthStore((state) => state.signIn);

  return useMutation({
    mutationFn: async (credentials: { email: string; password: string }) => {
      const { data } = await api.POST('/api/v1/auth/login', { body: credentials });
      // The token has to be live before /me is called, so set it first.
      session.setAccessToken(data!.access_token);
      const user = await fetchCurrentUser();
      return { user, accessToken: data!.access_token };
    },
    onSuccess: ({ user, accessToken }) => signIn(user, accessToken),
  });
}

export function useRegister() {
  const signIn = useAuthStore((state) => state.signIn);

  return useMutation({
    mutationFn: async (details: {
      email: string;
      password: string;
      display_name?: string | null;
    }) => {
      const { data } = await api.POST('/api/v1/auth/register', { body: details });
      session.setAccessToken(data!.access_token);
      const user = await fetchCurrentUser();
      return { user, accessToken: data!.access_token };
    },
    onSuccess: ({ user, accessToken }) => signIn(user, accessToken),
  });
}

export function useLogout() {
  const signOut = useAuthStore((state) => state.signOut);
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      await api.POST('/api/v1/auth/logout');
    },
    // Sign out locally whether or not the call succeeded: a network failure
    // must not strand the user in a session they asked to end.
    onSettled: () => {
      signOut();
      queryClient.clear();
    },
  });
}

export { fetchCurrentUser };
