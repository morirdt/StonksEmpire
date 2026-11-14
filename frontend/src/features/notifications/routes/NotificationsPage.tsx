import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import { useNavigate } from 'react-router';
import {
  notificationHref,
  useMarkAllRead,
  useMarkRead,
  useNotificationPages,
  useUnreadCount,
  type Notification,
} from '@/components/notifications/useNotifications';
import { formatAge } from '@/lib/format';

/**
 * Every notification, newest first, a page at a time.
 *
 * Pages follow the server's cursor rather than an offset, so a notification
 * arriving while someone reads does not shift the next page by one.
 */
export function NotificationsPage() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const navigate = useNavigate();
  const pages = useNotificationPages(unreadOnly);
  const count = useUnreadCount();
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();

  const items = pages.data?.pages.flatMap((page) => page.items) ?? [];

  function open(notification: Notification) {
    if (!notification.read_at) markRead.mutate(notification.id);
    const href = notificationHref(notification);
    if (href) void navigate(href);
  }

  return (
    <Stack spacing={3}>
      <Stack
        direction="row"
        sx={{ alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}
      >
        <Typography variant="h1">Notifications</Typography>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={unreadOnly ? 'unread' : 'all'}
            onChange={(_, value: string | null) => value && setUnreadOnly(value === 'unread')}
            aria-label="Which notifications"
          >
            <ToggleButton value="all">All</ToggleButton>
            <ToggleButton value="unread">Unread</ToggleButton>
          </ToggleButtonGroup>
          <Button
            variant="outlined"
            size="small"
            disabled={!count.data}
            loading={markAllRead.isPending}
            onClick={() => markAllRead.mutate()}
          >
            Mark all read
          </Button>
        </Stack>
      </Stack>

      {pages.isError && <Alert severity="error">{pages.error.message}</Alert>}

      {pages.isSuccess && items.length === 0 && (
        <Typography color="text.secondary">
          {unreadOnly
            ? 'Nothing unread.'
            : 'No notifications yet. Alerts you set will report here.'}
        </Typography>
      )}

      {items.length > 0 && (
        <Card variant="outlined">
          <List disablePadding>
            {items.map((notification) => (
              <ListItemButton
                key={notification.id}
                divider
                onClick={() => open(notification)}
                aria-label={`${notification.title}${notification.read_at ? '' : ', unread'}`}
              >
                <ListItemText
                  primary={notification.title}
                  secondary={`${notification.body} · ${formatAge(notification.created_at)}`}
                  slotProps={{
                    primary: { sx: { fontWeight: notification.read_at ? 400 : 600 } },
                  }}
                />
              </ListItemButton>
            ))}
          </List>
        </Card>
      )}

      {pages.hasNextPage && (
        <Button
          sx={{ alignSelf: 'center' }}
          loading={pages.isFetchingNextPage}
          onClick={() => void pages.fetchNextPage()}
        >
          Load older
        </Button>
      )}
    </Stack>
  );
}
