/**
 * System notifications on this computer when an alert fires.
 *
 * The trigger is the unread count going up, not the socket message itself. A
 * push is a hint, not data, and polling is the fallback: by watching the count
 * this hook works the same whether the badge moved because of a
 * `notification_created` push or because the socket was down and the count
 * polled. When the count rises it refetches the unread notifications over REST
 * and shows the ones this tab has not shown yet.
 *
 * The browser Notification API only works while a tab is open. Notifications
 * with the app closed would need Web Push (a service worker and server-side
 * VAPID sending), which this does not do.
 *
 * Whether it is on is a per-browser preference, so it lives in `localStorage`,
 * which is exactly as wide as the permission it depends on. Every access is
 * guarded: storage can throw, or come back empty, in a private window.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { api } from '@/lib/api/client';
import { notificationHref, type Notification } from './useNotifications';

const PREFERENCE_KEY = 'stonks-empire:desktop-notifications';
/** Enough for a burst from one end-of-day run; older ones stay in the bell. */
const BATCH_LIMIT = 20;

export type DesktopPermission = NotificationPermission | 'unsupported';

function supported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

function readPermission(): DesktopPermission {
  return supported() ? window.Notification.permission : 'unsupported';
}

function readPreference(): boolean {
  try {
    return window.localStorage.getItem(PREFERENCE_KEY) === 'on';
  } catch {
    return false;
  }
}

function writePreference(on: boolean) {
  try {
    window.localStorage.setItem(PREFERENCE_KEY, on ? 'on' : 'off');
  } catch {
    // Not remembered across reloads, but still on for this one.
  }
}

/** One system notification. Returns false where the constructor is unavailable. */
function show(title: string, body: string, tag: string, onClick?: () => void): boolean {
  try {
    const notification = new window.Notification(title, { body, tag, icon: '/favicon.svg' });
    notification.onclick = () => {
      window.focus();
      onClick?.();
      notification.close();
    };
    return true;
  } catch {
    // Android Chrome only allows notifications from a service worker.
    return false;
  }
}

export interface DesktopNotifications {
  permission: DesktopPermission;
  /** The user's switch, and the browser allows it. */
  active: boolean;
  enable: () => Promise<void>;
  disable: () => void;
}

/**
 * Owns the setting and does the watching. Call it once, from the bell, with
 * the unread count it already has.
 */
export function useDesktopNotifications(unread: number | undefined): DesktopNotifications {
  const navigate = useNavigate();
  const [permission, setPermission] = useState<DesktopPermission>(readPermission);
  const [preferred, setPreferred] = useState(readPreference);
  const active = preferred && permission === 'granted';

  // Ids this tab already knows about. `null` until the first fetch, which only
  // records: notifications from before the page loaded are not news.
  const seen = useRef<Set<string> | null>(null);
  const previous = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!active) {
      seen.current = null;
      previous.current = undefined;
      return;
    }
    if (unread === undefined) return;
    const rose = previous.current === undefined || unread > previous.current;
    previous.current = unread;
    if (!rose && seen.current !== null) return;

    let cancelled = false;
    void (async () => {
      // A one-off read for a side effect, deliberately outside the query
      // cache: nothing renders it, and the bell's own queries are already
      // invalidated by whatever moved the count.
      const { data } = await api.GET('/api/v1/me/notifications', {
        params: { query: { unread: true, limit: BATCH_LIMIT } },
      });
      if (cancelled || !data) return;
      const items: Notification[] = data.items;
      if (seen.current === null) {
        seen.current = new Set(items.map((item) => item.id));
        return;
      }
      // Oldest first, so a burst arrives in the order it happened.
      for (const item of [...items].reverse()) {
        if (seen.current.has(item.id)) continue;
        seen.current.add(item.id);
        const href = notificationHref(item);
        show(item.title, item.body, item.id, href ? () => void navigate(href) : undefined);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [active, unread, navigate]);

  const enable = useCallback(async () => {
    if (!supported()) return;
    const result =
      window.Notification.permission === 'default'
        ? await window.Notification.requestPermission()
        : window.Notification.permission;
    setPermission(result);
    if (result !== 'granted') return;
    writePreference(true);
    setPreferred(true);
    show(
      'Desktop alerts are on',
      'Alerts that fire will appear here while Stonks Empire is open.',
      'desktop-alerts-on',
    );
  }, []);

  const disable = useCallback(() => {
    writePreference(false);
    setPreferred(false);
  }, []);

  return { permission, active, enable, disable };
}
