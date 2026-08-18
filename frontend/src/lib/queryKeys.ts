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
} as const;
