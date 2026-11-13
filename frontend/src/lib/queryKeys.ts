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
    // Keyed by ticker *and* resolved range: the same symbol at 1M and at 1Y are
    // different windows, and sharing one entry would serve whichever loaded
    // last. Bars and indicators are separate keys because they are separate
    // requests — invalidating one must not refetch the other.
    bars: (ticker: string, range: string) => ['symbols', 'bars', ticker, range] as const,
    indicators: (ticker: string, range: string) =>
      ['symbols', 'indicators', ticker, range] as const,
  },
  chart: {
    all: () => ['chart'] as const,
    // One row per user, global across symbols — so no ticker in the key.
    preferences: () => ['chart', 'preferences'] as const,
  },
  quotes: {
    all: () => ['quotes'] as const,
    // Sorted so that ['AAPL','MSFT'] and ['MSFT','AAPL'] share one cache entry
    // rather than fetching the same prices twice under two keys.
    byTickers: (tickers: string[]) => ['quotes', [...tickers].sort().join(',')] as const,
  },
  screener: {
    all: () => ['screener'] as const,
    // The catalogue changes only when a column is added or a new exchange
    // appears in the universe, so it is one key with no parameters.
    fields: () => ['screener', 'fields'] as const,
    // A run is keyed by the filter tree *and* the sort, serialised: two
    // different screens must not share a cache entry, and the same screen run
    // twice must.
    run: (signature: string) => ['screener', 'run', signature] as const,
    presets: () => ['screener', 'presets'] as const,
    preset: (id: string) => ['screener', 'preset', id] as const,
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
