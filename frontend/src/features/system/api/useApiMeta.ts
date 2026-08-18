import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';

/** Fetches API identity — the end-to-end proof that the stack is wired up. */
export function useApiMeta() {
  return useQuery({
    queryKey: queryKeys.system.meta(),
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/meta');
      return data!;
    },
  });
}
