import { useEffect, useSyncExternalStore } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { API_BASE_URL, refreshAccessToken } from '@/lib/api/client';
import { RealtimeConnection, type RealtimeStatus } from './connection';

/** `http://host:8000` -> `ws://host:8000/api/v1/ws`, and https -> wss. */
export const REALTIME_URL = `${API_BASE_URL.replace(/^http/, 'ws')}/api/v1/ws`;

/**
 * The connection's status, as an external store, so any component can ask
 * "is the socket up?" without holding the connection itself.
 */
let currentStatus: RealtimeStatus = 'idle';
const statusListeners = new Set<() => void>();

function setStatus(next: RealtimeStatus) {
  currentStatus = next;
  for (const listener of statusListeners) listener();
}

function subscribeStatus(listener: () => void) {
  statusListeners.add(listener);
  return () => statusListeners.delete(listener);
}

/**
 * Opens the tab's one socket while `enabled`. Called once, by `AppLayout`,
 * with "is the user signed in".
 */
export function useRealtime(enabled: boolean) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled) return;
    const connection = new RealtimeConnection({
      url: REALTIME_URL,
      queryClient,
      refresh: refreshAccessToken,
    });
    const unsubscribe = connection.subscribe(setStatus);
    connection.start();
    return () => {
      unsubscribe();
      connection.stop();
      setStatus('idle');
    };
  }, [enabled, queryClient]);
}

/**
 * True while pushes are flowing.
 *
 * **Polling is the fallback, not something to delete.** A consumer that used to
 * poll keeps its interval and applies it only while this is false, so the grid
 * keeps moving when Redis, the socket, or the network is down.
 */
export function useRealtimeConnected(): boolean {
  return useSyncExternalStore(subscribeStatus, () => currentStatus) === 'open';
}
