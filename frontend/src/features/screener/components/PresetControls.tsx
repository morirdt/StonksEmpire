import { useState } from 'react';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import DeleteIcon from '@mui/icons-material/Delete';
import type { PresetSummary } from '../api/useScreener';

interface PresetControlsProps {
  presets: PresetSummary[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onSave: (name: string) => void;
  onDelete: (id: string) => void;
  saving?: boolean;
  canSave: boolean;
}

/**
 * Saving, loading, and deleting a screen.
 *
 * The listing carries names and timestamps only — never the filter trees — so
 * this renders even when one of the saved screens no longer validates. Opening
 * that one is what surfaces the problem, with a message the user can act on.
 */
export function PresetControls({
  presets,
  selectedId,
  onSelect,
  onSave,
  onDelete,
  saving,
  canSave,
}: PresetControlsProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');

  const save = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onSave(trimmed);
    setName('');
    setDialogOpen(false);
  };

  return (
    <>
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="body2" color="text.secondary">
          Saved screens
        </Typography>
        <Select
          size="small"
          displayEmpty
          value={selectedId ?? ''}
          onChange={(event) => onSelect(event.target.value || null)}
          inputProps={{ 'aria-label': 'Saved screens' }}
          sx={{ minWidth: 220 }}
        >
          <MenuItem value="">
            <em>{presets.length === 0 ? 'None saved yet' : 'New screen'}</em>
          </MenuItem>
          {presets.map((preset) => (
            <MenuItem key={preset.id} value={preset.id}>
              {preset.name}
            </MenuItem>
          ))}
        </Select>

        {selectedId && (
          <Tooltip title="Delete this saved screen">
            <IconButton
              size="small"
              onClick={() => onDelete(selectedId)}
              aria-label="Delete screen"
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}

        <Button size="small" onClick={() => setDialogOpen(true)} disabled={!canSave}>
          Save as…
        </Button>
      </Stack>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle>Save this screen</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && save()}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button onClick={save} loading={saving} variant="contained">
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
