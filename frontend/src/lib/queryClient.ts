import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api/client';

/**
 * Retrying a 4xx just delays showing the user a real error, so retries are
 * limited to genuinely transient failures.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) return false;
        return failureCount < 2;
      },
    },
    mutations: { retry: false },
  },
});
