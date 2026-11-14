import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import type { components } from '@/lib/api/schema';

export type AlertSummary = components['schemas']['AlertSummary'];
export type AlertDetail = components['schemas']['AlertResponse'];
export type AlertCreated = components['schemas']['AlertCreateResponse'];
export type AlertCreateInput = components['schemas']['AlertCreateRequest'];
export type AlertUpdateInput = components['schemas']['AlertUpdateRequest'];
export type AlertKind = components['schemas']['AlertKind'];
export type AlertTrigger = components['schemas']['AlertTriggerResponse'];
export type ConditionKind = Exclude<AlertKind, 'screen'>;
export type PresetOption = components['schemas']['ScreenerPresetListItem'];

/**
 * The list. A `notification_created` push invalidates `queryKeys.alerts.all()`,
 * so "last triggered" moves without a reload. "Last checked" moves on its own
 * schedule (each poll or nightly run), which a minute's polling covers.
 */
export function useAlerts() {
  return useQuery({
    queryKey: queryKeys.alerts.list(),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/alerts');
      return data!;
    },
  });
}

export function useAlert(id: string | null) {
  return useQuery({
    queryKey: queryKeys.alerts.detail(id ?? ''),
    enabled: id !== null,
    retry: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/alerts/{alert_id}', {
        params: { path: { alert_id: id! } },
      });
      return data!;
    },
  });
}

/** The field catalogue for one condition kind: the builder is generated from it. */
export function useAlertFields(kind: ConditionKind | null) {
  return useQuery({
    queryKey: queryKeys.alerts.fields(kind ?? ''),
    enabled: kind !== null,
    staleTime: Infinity,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/alerts/fields', {
        params: { query: { kind: kind! } },
      });
      return data!;
    },
  });
}

/**
 * The caller's saved screens, for a screen alert's target.
 *
 * Fetched here rather than imported from `features/screener`, which features
 * may not do. The key is the screener's own, so both share one cache entry.
 */
export function usePresetOptions(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.screener.presets(),
    enabled,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/screener/presets');
      return data!;
    },
  });
}

export function useAlertTriggers(id: string) {
  return useInfiniteQuery({
    queryKey: queryKeys.alerts.triggers(id),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data } = await api.GET('/api/v1/alerts/{alert_id}/triggers', {
        params: { path: { alert_id: id }, query: pageParam ? { cursor: pageParam } : {} },
      });
      return data!;
    },
    getNextPageParam: (page) => page.next_cursor,
  });
}

export function useCreateAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: AlertCreateInput) => {
      const { data } = await api.POST('/api/v1/alerts', { body: input });
      return data!;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
      // A screen alert changes its preset's alert_count.
      void queryClient.invalidateQueries({ queryKey: queryKeys.screener.presets() });
    },
  });
}

export function useUpdateAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: AlertUpdateInput }) => {
      const { data } = await api.PATCH('/api/v1/alerts/{alert_id}', {
        params: { path: { alert_id: id } },
        body: patch,
      });
      return data!;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() }),
  });
}

export function useDeleteAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.DELETE('/api/v1/alerts/{alert_id}', { params: { path: { alert_id: id } } });
      return id;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.screener.presets() });
    },
  });
}
