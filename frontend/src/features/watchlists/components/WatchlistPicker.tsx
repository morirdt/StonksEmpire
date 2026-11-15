import { useState } from 'react';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import AddIcon from '@mui/icons-material/Add';
import type { Watchlist } from '../api/useWatchlists';

interface WatchlistPickerProps {
  watchlists: Watchlist[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  onCreate: (name: string) => Promise<unknown>;
  creating?: boolean;
  createError?: string | null;
}

/** Chips rather than a dropdown: at this count, one click beats two. */
export function WatchlistPicker({
  watchlists,
  selectedId,
  onSelect,
  onCreate,
  creating = false,
  createError = null,
}: WatchlistPickerProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await onCreate(trimmed);
      setName('');
      setDialogOpen(false);
    } catch {
      // The error is rendered from createError; the dialog stays open so the
      // user can fix the name rather than retyping it.
    }
  };

  return (
    <>
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
        {watchlists.map((watchlist) => (
          <Chip
            key={watchlist.id}
            label={`${watchlist.name} (${watchlist.item_count})`}
            color={watchlist.id === selectedId ? 'primary' : 'default'}
            variant={watchlist.id === selectedId ? 'filled' : 'outlined'}
            onClick={() => onSelect(watchlist.id)}
          />
        ))}
        <Button size="small" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
          New list
        </Button>
      </Stack>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>New watchlist</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label="Name"
            value={name}
            error={Boolean(createError)}
            helperText={createError ?? ' '}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void submit();
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" loading={creating} onClick={() => void submit()}>
            Create
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
