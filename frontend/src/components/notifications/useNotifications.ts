/**
 * The notification hooks.
 *
 * In `components/` rather than a feature because the bell in `AppLayout` needs
 * them, and `components/` may not import a feature. The full list page in
 * `features/notifications` imports them from here.
 *
 * None of these poll while the realtime socket is up: a `notification_created`
 * push invalidates `queryKeys.notifications.all()`. While it is down, the count
 * falls back to polling, so the badge still moves.
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { queryKeys } from '@/lib/queryKeys';
import { useRealtimeConnected } from '@/lib/realtime/useRealtime';
import type { components } from '@/lib/api/schema';

export type Notification = components['schemas']['NotificationResponse'];

const FALLBACK_POLL_MS = 60_000;
export const RECENT_LIMIT = 5;
const PAGE_SIZE = 20;

export function useUnreadCount() {
  const pushed = useRealtimeConnected();
  return useQuery({
    queryKey: queryKeys.notifications.unreadCount(),
    refetchInterval: pushed ? false : FALLBACK_POLL_MS,
    refetchIntervalInBackground: false,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/me/notifications/unread-count');
      return data!.count;
    },
  });
}

export function useRecentNotifications(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.notifications.recent(),
    enabled,
    queryFn: async () => {
      const { data } = await api.GET('/api/v1/me/notifications', {
        params: { query: { limit: RECENT_LIMIT } },
      });
      return data!.items;
    },
  });
}

/** Newest first, a page at a time, following the server's cursor. */
export function useNotificationPages(unreadOnly: boolean) {
  return useInfiniteQuery({
    queryKey: queryKeys.notifications.list(unreadOnly),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const { data } = await api.GET('/api/v1/me/notifications', {
        params: {
          query: {
            unread: unreadOnly,
            limit: PAGE_SIZE,
            ...(pageParam ? { cursor: pageParam } : {}),
          },
        },
      });
      return data!;
    },
    getNextPageParam: (page) => page.next_cursor,
  });
}

export function useMarkRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await api.POST('/api/v1/me/notifications/{notification_id}/read', {
        params: { path: { notification_id: id } },
      });
      return data!;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all() }),
  });
}

export function useMarkAllRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await api.POST('/api/v1/me/notifications/read-all');
      return data!.updated;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all() }),
  });
}

/** Where a notification leads: its alert's history, while the alert exists. */
export function notificationHref(notification: Notification): string | null {
  return notification.alert_id ? `/alerts/${notification.alert_id}` : null;
}
