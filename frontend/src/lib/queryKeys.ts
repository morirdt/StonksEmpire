/**
 * Every query key in the app is built here.
 *
 * Centralising them keeps invalidation precise — `queryKeys.watchlists.all`
 * invalidates a whole feature, while a detail key invalidates one row.
 */
export const queryKeys = {
  system: {
    meta: () => ['system', 'meta'] as const,
    health: () => ['system', 'health'] as const,
  },
  auth: {
    all: () => ['auth'] as const,
    currentUser: () => ['auth', 'me'] as const,
  },
  symbols: {
    all: () => ['symbols'] as const,
    search: (query: string, limit: number) => ['symbols', 'search', query, limit] as const,
    detail: (ticker: string) => ['symbols', 'detail', ticker] as const,
  },
  quotes: {
    all: () => ['quotes'] as const,
    // Sorted so that ['AAPL','MSFT'] and ['MSFT','AAPL'] share one cache entry
    // rather than fetching the same prices twice under two keys.
    byTickers: (tickers: string[]) => ['quotes', [...tickers].sort().join(',')] as const,
  },
  watchlists: {
    all: () => ['watchlists'] as const,
    list: () => ['watchlists', 'list'] as const,
    detail: (id: string) => ['watchlists', 'detail', id] as const,
    // Separate from `detail` because the grid polls and the metadata does not:
    // invalidating one must not force a refetch of the other.
    grid: (id: string) => ['watchlists', 'grid', id] as const,
  },
} as const;
