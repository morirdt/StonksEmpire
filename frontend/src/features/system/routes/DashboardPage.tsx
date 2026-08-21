import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { ApiError } from '@/lib/api/client';
import { useApiMeta } from '@/features/system/api/useApiMeta';

export function DashboardPage() {
  const { data, isPending, error } = useApiMeta();

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h1" gutterBottom>
          Stonks Empire
        </Typography>
        <Typography variant="body1" color="text.secondary">
          Phase 0 scaffold. Watchlists, screeners, alerts, and the trading journal land in later
          phases.
        </Typography>
      </Box>

      <Card sx={{ maxWidth: 480 }}>
        <CardContent>
          <Typography variant="h3" gutterBottom>
            API connection
          </Typography>

          {isPending && <Skeleton variant="rounded" height={72} />}

          {error && (
            <Alert severity="error">
              Could not reach the API
              {error instanceof ApiError && error.correlationId
                ? ` (correlation id: ${error.correlationId})`
                : ''}
              . Is it running on {import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'}?
            </Alert>
          )}

          {data && (
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
              <Chip label={`v${data.version}`} color="primary" size="small" />
              <Chip label={data.environment} size="small" variant="outlined" />
              <Chip label={`api ${data.api_version}`} size="small" variant="outlined" />
              <Chip label="connected" color="success" size="small" />
            </Stack>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
}
