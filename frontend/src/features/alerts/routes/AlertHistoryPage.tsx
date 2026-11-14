import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Link from '@mui/material/Link';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { Link as RouterLink, useParams } from 'react-router';
import { formatAge, formatPrice } from '@/lib/format';
import { useAlert, useAlertTriggers, type AlertTrigger } from '../api/useAlerts';

/**
 * One firing, and the values that caused it.
 *
 * A symbol alert's snapshot holds the numbers its condition named, as decimal
 * strings, and they are parsed only in `lib/format.ts`. A screen alert's
 * snapshot holds the symbols that newly matched.
 */
function TriggerCard({ trigger }: { trigger: AlertTrigger }) {
  const snapshot = trigger.snapshot as {
    values?: Record<string, string | null>;
    entrants?: string[];
    matched?: number;
    condition?: string;
  };
  const when = new Date(trigger.triggered_at).toLocaleString();

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={1}>
          <Typography variant="subtitle2">
            {when}
            <Typography component="span" variant="body2" color="text.secondary">
              {' · '}
              {formatAge(trigger.triggered_at)}
              {trigger.as_of_date ? ` · close of ${trigger.as_of_date}` : ''}
            </Typography>
          </Typography>
          {snapshot.entrants && (
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
              {snapshot.entrants.map((ticker) => (
                <Chip
                  key={ticker}
                  size="small"
                  label={ticker}
                  component={RouterLink}
                  to={`/symbols/${ticker}`}
                  clickable
                />
              ))}
              {snapshot.matched !== undefined && (
                <Typography variant="body2" color="text.secondary">
                  {snapshot.matched} matching in total
                </Typography>
              )}
            </Stack>
          )}
          {snapshot.values && (
            <Stack
              component="dl"
              direction="row"
              sx={{ m: 0, flexWrap: 'wrap', columnGap: 3, rowGap: 0.5 }}
            >
              {Object.entries(snapshot.values).map(([key, value]) => (
                <Stack key={key} direction="row" spacing={1}>
                  <Typography component="dt" variant="body2" color="text.secondary">
                    {key}
                  </Typography>
                  <Typography
                    component="dd"
                    variant="body2"
                    sx={{ m: 0, fontVariantNumeric: 'tabular-nums' }}
                  >
                    {formatPrice(value)}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

export function AlertHistoryPage() {
  const { alertId = '' } = useParams();
  const alert = useAlert(alertId);
  const triggers = useAlertTriggers(alertId);
  const items = triggers.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <Stack spacing={3}>
      <Link component={RouterLink} to="/alerts" variant="body2">
        All alerts
      </Link>

      {alert.isPending && <CircularProgress size={24} />}
      {alert.isError && <Alert severity="error">{alert.error.message}</Alert>}
      {alert.data && (
        <Stack spacing={0.5}>
          <Typography variant="h1">{alert.data.name}</Typography>
          <Typography color="text.secondary">
            {alert.data.kind === 'screen'
              ? `New matches for “${alert.data.preset_name}”`
              : `${alert.data.ticker}: ${alert.data.condition_summary ?? ''}`}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Last checked{' '}
            {alert.data.last_evaluated_at ? formatAge(alert.data.last_evaluated_at) : 'not yet'}
            {alert.data.disabled_reason ? ` · Disabled: ${alert.data.disabled_reason}` : ''}
          </Typography>
        </Stack>
      )}

      <Typography variant="h2">History</Typography>
      {triggers.isSuccess && items.length === 0 && (
        <Typography color="text.secondary">It has not fired yet.</Typography>
      )}
      <Stack spacing={1.5}>
        {items.map((trigger) => (
          <TriggerCard key={trigger.id} trigger={trigger} />
        ))}
      </Stack>
      {triggers.hasNextPage && (
        <Button
          sx={{ alignSelf: 'center' }}
          loading={triggers.isFetchingNextPage}
          onClick={() => void triggers.fetchNextPage()}
        >
          Load older
        </Button>
      )}
    </Stack>
  );
}
