import { useState } from 'react';
import Badge from '@mui/material/Badge';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Divider from '@mui/material/Divider';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import Popover from '@mui/material/Popover';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import NotificationsIcon from '@mui/icons-material/Notifications';
import { Link as RouterLink, useNavigate } from 'react-router';
import { formatAge } from '@/lib/format';
import {
  notificationHref,
  useMarkAllRead,
  useMarkRead,
  useRecentNotifications,
  useUnreadCount,
  type Notification,
} from './useNotifications';

/**
 * The unread badge, and a popover of the most recent notifications.
 *
 * The count updates without a reload: a `notification_created` push
 * invalidates it, or, with the socket down, it polls. Opening an item marks it
 * read and goes to its alert's history.
 */
export function NotificationBell() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const navigate = useNavigate();
  const count = useUnreadCount();
  const recent = useRecentNotifications(anchor !== null);
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();

  const unread = count.data ?? 0;
  const label = unread === 0 ? 'Notifications' : `Notifications, ${unread} unread`;

  function open(notification: Notification) {
    if (!notification.read_at) markRead.mutate(notification.id);
    const href = notificationHref(notification);
    setAnchor(null);
    if (href) void navigate(href);
  }

  return (
    <>
      <Tooltip title="Notifications">
        <IconButton
          size="small"
          aria-label={label}
          onClick={(event) => setAnchor(event.currentTarget)}
        >
          <Badge badgeContent={unread} color="primary" max={99}>
            <NotificationsIcon fontSize="small" />
          </Badge>
        </IconButton>
      </Tooltip>
      <Popover
        open={anchor !== null}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { width: 360, maxWidth: 'calc(100vw - 32px)' } } }}
      >
        <Stack
          direction="row"
          sx={{ px: 2, py: 1, alignItems: 'center', justifyContent: 'space-between' }}
        >
          <Typography variant="subtitle2">Notifications</Typography>
          <Button
            size="small"
            disabled={unread === 0}
            loading={markAllRead.isPending}
            onClick={() => markAllRead.mutate()}
          >
            Mark all read
          </Button>
        </Stack>
        <Divider />
        {recent.data?.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            Nothing yet. Alerts you set will report here.
          </Typography>
        )}
        <List dense disablePadding>
          {recent.data?.map((notification) => (
            <ListItemButton
              key={notification.id}
              onClick={() => open(notification)}
              sx={{ alignItems: 'flex-start', gap: 1 }}
            >
              <Box
                aria-hidden
                sx={{
                  mt: 1,
                  width: 8,
                  height: 8,
                  flexShrink: 0,
                  borderRadius: '50%',
                  bgcolor: notification.read_at ? 'transparent' : 'primary.main',
                }}
              />
              <ListItemText
                primary={notification.title}
                secondary={`${notification.body} · ${formatAge(notification.created_at)}`}
                slotProps={{
                  primary: { sx: { fontWeight: notification.read_at ? 400 : 600 } },
                  secondary: {
                    sx: {
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    },
                  },
                }}
              />
            </ListItemButton>
          ))}
        </List>
        <Divider />
        <Box sx={{ p: 1, textAlign: 'center' }}>
          <Button
            size="small"
            component={RouterLink}
            to="/notifications"
            onClick={() => setAnchor(null)}
          >
            See all
          </Button>
        </Box>
      </Popover>
    </>
  );
}
