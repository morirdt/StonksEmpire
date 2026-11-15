import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import type { components } from '@/lib/api/schema';

export type Symbol = components['schemas']['SymbolResponse'];

const MIN_QUERY_LENGTH = 1;

/**
 * Search the symbol universe.
 *
 * `keepPreviousData` matters more than usual here: this backs a type-ahead, and
 * without it the results list empties on every keystroke and the box flickers
 * between "found things" and "found nothing".
 */
export function useSymbolSearch(query: string, { limit = 10 } = {}) {
  const trimmed = query.trim();

  return useQuery({
    queryKey: queryKeys.symbols.search(trimmed, limit),
    enabled: trimmed.length >= MIN_QUERY_LENGTH,
    placeholderData: keepPreviousData,
    // Symbol metadata changes at most once a day, when the seed runs.
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/symbols', {
        params: { query: { search: trimmed, limit } },
      });
      return data!.items;
    },
  });
}
