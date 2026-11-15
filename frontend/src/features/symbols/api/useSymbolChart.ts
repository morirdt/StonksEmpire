/**
 * The two chart reads.
 *
 * Two queries rather than one, mirroring the two endpoints: they fire in
 * parallel, and Phase 4 will want indicators without bars. Both are keyed by
 * ticker *and* range, because the same symbol over two windows is two different
 * answers.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import type { components } from '@/lib/api/schema';

export type Bar = components['schemas']['BarResponse'];
export type Indicator = components['schemas']['IndicatorResponse'];
export type ChartRange = components['schemas']['ChartRange'];

/** The toolbar's options, widest last. */
export const CHART_RANGES: ChartRange[] = ['1M', '3M', '6M', '1Y', '2Y', 'MAX'];

/**
 * Bars and indicators change once a day, after the close. Refetching them on
 * every window focus would spend round trips re-fetching yesterday.
 */
const STALE_TIME_MS = 5 * 60_000;

export function useBars(ticker: string, range: ChartRange) {
  return useQuery({
    queryKey: queryKeys.symbols.bars(ticker, range),
    staleTime: STALE_TIME_MS,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/symbols/{ticker}/bars', {
        params: { path: { ticker }, query: { range } },
      });
      return data!;
    },
  });
}

export function useIndicators(ticker: string, range: ChartRange) {
  return useQuery({
    queryKey: queryKeys.symbols.indicators(ticker, range),
    staleTime: STALE_TIME_MS,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/symbols/{ticker}/indicators', {
        params: { path: { ticker }, query: { range } },
      });
      return data!;
    },
  });
}

/** The symbol's own row, for the page heading and the "unknown ticker" case. */
export function useSymbol(ticker: string) {
  return useQuery({
    queryKey: queryKeys.symbols.detail(ticker),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/symbols/{ticker}', {
        params: { path: { ticker } },
      });
      return data!;
    },
  });
}
