import { useEffect, useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { SymbolSearch } from '@/features/symbols/components/SymbolSearch';
import { ApiError } from '@/lib/api/client';
import { QuoteGrid } from '../components/QuoteGrid';
import { WatchlistPicker } from '../components/WatchlistPicker';
import {
  useAddWatchlistItem,
  useCreateWatchlist,
  useRemoveWatchlistItem,
  useReorderWatchlist,
  useWatchlistGrid,
  useWatchlists,
} from '../api/useWatchlists';

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.message;
  return 'Something went wrong. Please try again.';
}

/** Nothing exists yet — so say what to do, not just that the list is empty. */
function NoWatchlists({ onCreate, creating }: { onCreate: () => void; creating: boolean }) {
  return (
    <Card>
      <CardContent sx={{ textAlign: 'center', py: 6 }}>
        <Typography variant="h2" gutterBottom>
          No watchlists yet
        </Typography>
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          A watchlist is a set of symbols you want to keep an eye on. Create one, then search for a
          ticker to add it.
        </Typography>
        <Button variant="contained" onClick={onCreate} loading={creating}>
          Create your first watchlist
        </Button>
      </CardContent>
    </Card>
  );
}

export function WatchlistsPage() {
  const watchlists = useWatchlists();
  const createWatchlist = useCreateWatchlist();
  const [selectedId, setSelectedId] = useState<string | undefined>();

  // Follow the server's ordering rather than pinning an id that may have been
  // deleted in another tab.
  const available = useMemo(() => watchlists.data ?? [], [watchlists.data]);
  useEffect(() => {
    const first = available[0];
    if (!first) {
      setSelectedId(undefined);
      return;
    }
    if (!selectedId || !available.some((w) => w.id === selectedId)) {
      setSelectedId(first.id);
    }
  }, [available, selectedId]);

  const grid = useWatchlistGrid(selectedId);
  const addItem = useAddWatchlistItem(selectedId ?? '');
  const removeItem = useRemoveWatchlistItem(selectedId ?? '');
  const reorder = useReorderWatchlist(selectedId ?? '');

  const rows = grid.data?.rows ?? [];
  const tickers = rows.map((row) => row.symbol.ticker);

  const move = (itemId: string, direction: -1 | 1) => {
    const order = rows.map((row) => row.item.id);
    const from = order.indexOf(itemId);
    const to = from + direction;
    const moved = order[from];
    const displaced = order[to];
    if (from < 0 || moved === undefined || displaced === undefined) return;

    order[from] = displaced;
    order[to] = moved;
    reorder.mutate(order);
  };

  if (watchlists.isPending) {
    return (
      <Stack sx={{ alignItems: 'center', py: 8 }}>
        <CircularProgress />
      </Stack>
    );
  }

  if (watchlists.isError) {
    return (
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" onClick={() => void watchlists.refetch()}>
            Retry
          </Button>
        }
      >
        {errorMessage(watchlists.error)}
      </Alert>
    );
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h1" gutterBottom>
          Watchlists
        </Typography>
        <Typography color="text.secondary">
          Prices refresh about once a minute while this tab is open.
        </Typography>
      </Box>

      {available.length === 0 ? (
        <NoWatchlists
          onCreate={() => createWatchlist.mutate('My watchlist')}
          creating={createWatchlist.isPending}
        />
      ) : (
        <>
          <WatchlistPicker
            watchlists={available}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onCreate={(name) => createWatchlist.mutateAsync(name)}
            creating={createWatchlist.isPending}
            createError={errorMessage(createWatchlist.error)}
          />

          <Box sx={{ maxWidth: 420 }}>
            <SymbolSearch
              onSelect={(symbol) => addItem.mutate(symbol.ticker)}
              disabled={!selectedId || addItem.isPending}
              excludeTickers={tickers}
            />
          </Box>

          {addItem.isError ? <Alert severity="error">{errorMessage(addItem.error)}</Alert> : null}
          {grid.isError ? (
            <Alert
              severity="error"
              action={
                <Button color="inherit" size="small" onClick={() => void grid.refetch()}>
                  Retry
                </Button>
              }
            >
              {errorMessage(grid.error)}
            </Alert>
          ) : null}

          <Box sx={{ height: 560, width: '100%' }}>
            <QuoteGrid
              rows={rows}
              loading={grid.isPending}
              onRemove={(itemId) => removeItem.mutate(itemId)}
              onMove={move}
              busy={reorder.isPending || removeItem.isPending}
            />
          </Box>
        </>
      )}
    </Stack>
  );
}
