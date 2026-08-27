/**
 * The screener's reads and writes.
 *
 * Two things here are worth knowing before changing them.
 *
 * **The catalogue is the only source of field metadata.** There is no field
 * list in TypeScript, on purpose: a second copy of the registry drifts the
 * first time a column is added, and the whole reason the backend has one
 * registry is that there should be exactly one list.
 *
 * **A run is a query, not a mutation**, even though it is a POST. It is a read
 * with a body — see the endpoint's own note on why — so it caches, dedupes and
 * refetches like every other read in this app. The key carries the serialised
 * filter tree and sort, so two different screens never share an entry.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import type { components } from '@/lib/api/schema';
import { filterTreeKey, type FilterNode, type ScreenerSort } from '../lib/filterTree';

export type ScreenerRunResponse = components['schemas']['ScreenerRunResponse'];
export type ScreenerColumn = components['schemas']['ScreenerColumn'];
export type ScreenerRow = components['schemas']['ScreenerRow'];
export type PresetSummary = components['schemas']['ScreenerPresetSummary'];
export type Preset = components['schemas']['ScreenerPresetResponse'];

/** The catalogue moves when a migration does, so an hour is conservative. */
const CATALOGUE_STALE_MS = 60 * 60_000;

/** Bars land once a day, so a screen re-run on focus would re-fetch yesterday. */
const RUN_STALE_MS = 5 * 60_000;

export function useScreenerFields() {
  return useQuery({
    queryKey: queryKeys.screener.fields(),
    staleTime: CATALOGUE_STALE_MS,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/screener/fields');
      return data!;
    },
  });
}

/**
 * Run an ad-hoc screen. Disabled until there is a runnable tree, so the "no
 * filters yet" state costs no request.
 */
export function useScreenerRun(tree: FilterNode | null, sort: ScreenerSort, limit: number) {
  return useQuery({
    queryKey: queryKeys.screener.run(filterTreeKey(tree, sort, limit)),
    enabled: tree !== null,
    staleTime: RUN_STALE_MS,
    queryFn: async () => {
      const { data } = await api.POST('/api/v1/screener/run', {
        body: { filters: tree!, sort, limit },
      });
      return data!;
    },
  });
}

/** Run what is saved under a name, rather than what the client thinks is saved. */
export function useRunPreset(presetId: string | null, limit: number) {
  return useQuery({
    queryKey: queryKeys.screener.run(`preset:${presetId}:${limit}`),
    enabled: presetId !== null,
    staleTime: RUN_STALE_MS,
    queryFn: async () => {
      const { data } = await api.POST('/api/v1/screener/presets/{preset_id}/run', {
        params: { path: { preset_id: presetId! } },
        body: { limit },
      });
      return data!;
    },
  });
}

export function useScreenerPresets() {
  return useQuery({
    queryKey: queryKeys.screener.presets(),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/screener/presets');
      return data!;
    },
  });
}

/**
 * One saved screen, with its tree.
 *
 * Separate from the listing because the listing deliberately does not carry
 * trees: a preset naming a field a later phase removed is a 422 *here* and a
 * perfectly good row *there*, which is what keeps the page loading.
 */
export function useScreenerPreset(presetId: string | null) {
  return useQuery({
    queryKey: queryKeys.screener.preset(presetId ?? ''),
    enabled: presetId !== null,
    retry: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/screener/presets/{preset_id}', {
        params: { path: { preset_id: presetId! } },
      });
      return data!;
    },
  });
}

export function useCreatePreset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: { name: string; filters: FilterNode; sort: ScreenerSort }) => {
      const { data } = await api.POST('/api/v1/screener/presets', { body: input });
      return data!;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.screener.presets() }),
  });
}

export function useUpdatePreset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: {
      id: string;
      name?: string;
      filters?: FilterNode;
      sort?: ScreenerSort;
    }) => {
      const { id, ...body } = input;
      const { data } = await api.PATCH('/api/v1/screener/presets/{preset_id}', {
        params: { path: { preset_id: id } },
        body,
      });
      return data!;
    },
    onSuccess: (preset) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.screener.presets() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.screener.preset(preset.id) });
    },
  });
}

export function useDeletePreset() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (presetId: string) => {
      await api.DELETE('/api/v1/screener/presets/{preset_id}', {
        params: { path: { preset_id: presetId } },
      });
      return presetId;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.screener.presets() }),
  });
}
