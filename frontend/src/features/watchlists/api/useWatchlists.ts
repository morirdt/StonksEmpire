import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import type { components } from '@/lib/api/schema';

export type Watchlist = components['schemas']['WatchlistResponse'];
export type WatchlistRow = components['schemas']['WatchlistRowResponse'];
export type WatchlistDetail = components['schemas']['WatchlistDetailResponse'];

/**
 * How often the grid re-polls, in milliseconds.
 *
 * Matched to the backend's quote TTL: polling faster than the TTL just serves
 * the same cached row again, so it costs a round trip and buys nothing. Phase 5
 * replaces this with a WebSocket and the interval goes away.
 */
export const GRID_POLL_INTERVAL_MS = 60_000;

export function useWatchlists() {
  return useQuery({
    queryKey: queryKeys.watchlists.list(),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/watchlists');
      return data!;
    },
  });
}

/**
 * The quote grid for one list.
 *
 * Polling pauses when the tab is hidden. Leaving it running would spend the
 * provider quota on a screen nobody is looking at, and every background tab
 * would keep doing so all day.
 */
export function useWatchlistGrid(watchlistId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.watchlists.grid(watchlistId ?? ''),
    enabled: Boolean(watchlistId),
    refetchInterval: GRID_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/watchlists/{watchlist_id}/quotes', {
        params: { path: { watchlist_id: watchlistId! } },
      });
      return data!;
    },
  });
}

export function useCreateWatchlist() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (name: string) => {
      const { data } = await api.POST('/api/v1/watchlists', {
        body: { name, is_default: false },
      });
      return data!;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.list() }),
  });
}

export function useRenameWatchlist() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) => {
      const { data } = await api.PATCH('/api/v1/watchlists/{watchlist_id}', {
        params: { path: { watchlist_id: id } },
        body: { name },
      });
      return data!;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all() }),
  });
}

export function useDeleteWatchlist() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (id: string) => {
      await api.DELETE('/api/v1/watchlists/{watchlist_id}', {
        params: { path: { watchlist_id: id } },
      });
      return id;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all() }),
  });
}

export function useAddWatchlistItem(watchlistId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (ticker: string) => {
      const { data } = await api.POST('/api/v1/watchlists/{watchlist_id}/items', {
        params: { path: { watchlist_id: watchlistId } },
        body: { ticker },
      });
      return data!;
    },
    // Both keys: the grid gains a row and the listing's item_count changes.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all() }),
  });
}

export function useRemoveWatchlistItem(watchlistId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (itemId: string) => {
      await api.DELETE('/api/v1/watchlists/{watchlist_id}/items/{item_id}', {
        params: { path: { watchlist_id: watchlistId, item_id: itemId } },
      });
      return itemId;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all() }),
  });
}

/**
 * Reorder the whole list.
 *
 * The API takes the complete ordering, so this sends every id. It also writes
 * the new order into the cache immediately: the user has just dragged a row,
 * and waiting a round trip to see it land makes the grid feel broken.
 */
export function useReorderWatchlist(watchlistId: string) {
  const queryClient = useQueryClient();
  const gridKey = queryKeys.watchlists.grid(watchlistId);

  return useMutation({
    mutationFn: async (itemIds: string[]) => {
      const { data } = await api.PATCH('/api/v1/watchlists/{watchlist_id}/items', {
        params: { path: { watchlist_id: watchlistId } },
        body: { item_ids: itemIds },
      });
      return data!;
    },
    onMutate: async (itemIds: string[]) => {
      await queryClient.cancelQueries({ queryKey: gridKey });
      const previous = queryClient.getQueryData<WatchlistDetail>(gridKey);
      if (previous) {
        const byId = new Map(previous.rows.map((row) => [row.item.id, row]));
        queryClient.setQueryData<WatchlistDetail>(gridKey, {
          ...previous,
          rows: itemIds.flatMap((id) => {
            const row = byId.get(id);
            return row ? [row] : [];
          }),
        });
      }
      return { previous };
    },
    onError: (_error, _itemIds, context) => {
      // Put the old order back rather than leaving the UI asserting something
      // the server rejected.
      if (context?.previous) queryClient.setQueryData(gridKey, context.previous);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: gridKey }),
  });
}
