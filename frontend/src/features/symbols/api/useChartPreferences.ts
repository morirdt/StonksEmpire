/**
 * The caller's chart preferences.
 *
 * One row per user, global across symbols, so there is no ticker in the key and
 * one query serves every chart the session opens.
 *
 * The save is optimistic — the cache is updated before the round trip, because
 * a toggle that waits for the server to agree feels broken. What it is *not* is
 * silently discarded on failure: the mutation rolls the cached row back so the
 * cache never claims something was saved that was not, and reports the failure
 * so the page can say so. The user's on-screen choice is separate local state
 * and survives regardless — it just will not be there after a reload.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import type { components } from '@/lib/api/schema';

export type ChartPreferences = components['schemas']['ChartPreferencesResponse'];

export function useChartPreferences() {
  return useQuery({
    queryKey: queryKeys.chart.preferences(),
    // The endpoint answers with defaults rather than 404 when nothing has been
    // saved, so there is no "not set yet" branch to write here.
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/me/chart-preferences');
      return data!;
    },
  });
}

export function useSaveChartPreferences() {
  const queryClient = useQueryClient();
  const key = queryKeys.chart.preferences();

  return useMutation({
    mutationFn: async (preferences: ChartPreferences) => {
      const { data } = await api.PUT('/api/v1/me/chart-preferences', { body: preferences });
      return data!;
    },
    onMutate: async (preferences) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<ChartPreferences>(key);
      queryClient.setQueryData<ChartPreferences>(key, preferences);
      return { previous };
    },
    onError: (_error, _preferences, context) => {
      // Put back what the server actually holds. The page keeps showing the
      // user's choice and surfaces the failure; what must not happen is the
      // cache asserting a save that never landed.
      if (context?.previous) queryClient.setQueryData(key, context.previous);
    },
    onSuccess: (saved) => queryClient.setQueryData(key, saved),
  });
}
