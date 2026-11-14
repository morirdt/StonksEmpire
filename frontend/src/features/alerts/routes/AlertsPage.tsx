import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import { Link as RouterLink, useLocation, useNavigate, useSearchParams } from 'react-router';
import { ApiError } from '@/lib/api/client';
import { formatAge } from '@/lib/format';
import {
  useAlert,
  useAlerts,
  useDeleteAlert,
  useUpdateAlert,
  type AlertCreated,
  type AlertSummary,
} from '../api/useAlerts';
import { AlertDialog } from '../components/AlertDialog';

const STATUS: Record<
  AlertSummary['status'],
  { label: string; color: 'success' | 'default' | 'info' | 'warning' }
> = {
  active: { label: 'Active', color: 'success' },
  paused: { label: 'Paused', color: 'default' },
  triggered: { label: 'Fired', color: 'info' },
  disabled: { label: 'Disabled', color: 'warning' },
};

const KIND: Record<AlertSummary['kind'], string> = {
  intraday: 'Intraday',
  end_of_day: 'End of day',
  screen: 'Screen',
};

/**
 * "Last checked" is the column that matters most.
 *
 * A dead worker leaves alerts silently never firing, which looks exactly like
 * "the condition was never met". An age that keeps growing is what tells a
 * user something is wrong, so it is always shown, never hidden behind status.
 */
function lastChecked(alert: AlertSummary): string {
  return alert.last_evaluated_at ? formatAge(alert.last_evaluated_at) : 'Not yet';
}

/**
 * Every alert, plus the create/edit dialog.
 *
 * `/alerts/new?ticker=X` and `/alerts/new?preset=<id>` open the dialog
 * prefilled: the symbol page and the screener link here rather than importing
 * this feature.
 */
export function AlertsPage({ creating = false }: { creating?: boolean }) {
  const alerts = useAlerts();
  const update = useUpdateAlert();
  const remove = useDeleteAlert();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [editingId, setEditingId] = useState<string | null>(null);
  const editing = useAlert(editingId);
  const [created, setCreated] = useState<AlertCreated | null>(null);

  const closeCreate = () => void navigate('/alerts', { replace: location.pathname !== '/alerts' });

  return (
    <Stack spacing={3}>
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
        <Typography variant="h1">Alerts</Typography>
        <Button variant="contained" component={RouterLink} to="/alerts/new">
          New alert
        </Button>
      </Stack>

      {created && (
        <Alert
          severity={created.condition_met_now ? 'info' : 'success'}
          onClose={() => setCreated(null)}
        >
          {created.condition_met_now
            ? `“${created.name}” is already true right now. It will fire the next time it becomes true, not for the current state.`
            : `“${created.name}” is set.`}
        </Alert>
      )}

      {alerts.isError && <Alert severity="error">{alerts.error.message}</Alert>}
      {(update.error ?? remove.error) && (
        <Alert severity="error">
          {(update.error ?? remove.error) instanceof ApiError
            ? (update.error ?? remove.error)!.message
            : 'Something went wrong.'}
        </Alert>
      )}

      {alerts.isSuccess && alerts.data.length === 0 && (
        <Typography color="text.secondary">
          No alerts yet. Create one here, from a symbol's page, or from a saved screen.
        </Typography>
      )}

      {alerts.isSuccess && alerts.data.length > 0 && (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small" aria-label="Alerts">
            <TableHead>
              <TableRow>
                <TableCell>Alert</TableCell>
                <TableCell>Watching</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Last checked</TableCell>
                <TableCell>Last fired</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {alerts.data.map((alert) => {
                const paused = alert.status === 'paused';
                const canResume = alert.status !== 'active';
                return (
                  <TableRow key={alert.id} hover>
                    <TableCell>
                      <Stack>
                        <RouterLinkText to={`/alerts/${alert.id}`}>{alert.name}</RouterLinkText>
                        <Typography variant="caption" color="text.secondary">
                          {KIND[alert.kind]}
                          {alert.trigger_mode === 'rearm' && alert.kind !== 'screen'
                            ? ` · repeats, ${alert.cooldown_minutes} min quiet`
                            : ''}
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {alert.kind === 'screen' ? alert.preset_name : alert.ticker}
                      </Typography>
                      {alert.condition_summary && (
                        <Typography variant="caption" color="text.secondary">
                          {alert.condition_summary}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Tooltip title={alert.disabled_reason ?? ''}>
                        <Chip
                          size="small"
                          label={STATUS[alert.status].label}
                          color={STATUS[alert.status].color}
                        />
                      </Tooltip>
                    </TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {lastChecked(alert)}
                    </TableCell>
                    <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>
                      {alert.last_triggered_at ? formatAge(alert.last_triggered_at) : 'Never'}
                    </TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                      {canResume ? (
                        <Tooltip title={alert.status === 'triggered' ? 'Re-arm' : 'Resume'}>
                          <IconButton
                            size="small"
                            aria-label={`Resume ${alert.name}`}
                            onClick={() =>
                              update.mutate({ id: alert.id, patch: { status: 'active' } })
                            }
                          >
                            <PlayArrowIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      ) : (
                        <Tooltip title="Pause">
                          <IconButton
                            size="small"
                            aria-label={`Pause ${alert.name}`}
                            disabled={paused}
                            onClick={() =>
                              update.mutate({ id: alert.id, patch: { status: 'paused' } })
                            }
                          >
                            <PauseIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                      <Tooltip title="Edit">
                        <IconButton
                          size="small"
                          aria-label={`Edit ${alert.name}`}
                          onClick={() => setEditingId(alert.id)}
                        >
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton
                          size="small"
                          aria-label={`Delete ${alert.name}`}
                          onClick={() => remove.mutate(alert.id)}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {creating && (
        <AlertDialog
          initialTicker={params.get('ticker') ?? undefined}
          initialPresetId={params.get('preset') ?? undefined}
          onClose={closeCreate}
          onCreated={setCreated}
        />
      )}

      {editingId && editing.isError && (
        <Alert severity="error" onClose={() => setEditingId(null)}>
          {editing.error.message}
        </Alert>
      )}
      {editingId && editing.data && (
        <AlertDialog
          key={editing.data.id}
          editing={editing.data}
          onClose={() => setEditingId(null)}
        />
      )}
    </Stack>
  );
}

function RouterLinkText({ to, children }: { to: string; children: string }) {
  return (
    <Typography
      component={RouterLink}
      to={to}
      variant="body2"
      color="primary"
      sx={{ fontWeight: 500 }}
    >
      {children}
    </Typography>
  );
}
