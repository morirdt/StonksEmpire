import { useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import { FilterBuilder } from '@/components/filter-builder/FilterBuilder';
import {
  buildFilterTree,
  parseFilterTree,
  type FilterRow,
  type GroupOperator,
} from '@/components/filter-builder/filterTree';
import { SymbolSearch } from '@/components/SymbolSearch';
import { ApiError } from '@/lib/api/client';
import {
  useAlertFields,
  useCreateAlert,
  usePresetOptions,
  useUpdateAlert,
  type AlertCreated,
  type AlertDetail,
  type AlertKind,
  type ConditionKind,
} from '../api/useAlerts';

type TriggerMode = 'once' | 'rearm';

const KIND_LABELS: Record<AlertKind, { label: string; help: string }> = {
  intraday: {
    label: 'Intraday',
    help: 'Checked every minute while the market is open, against the live price.',
  },
  end_of_day: {
    label: 'End of day',
    help: "Checked each evening against the day's close and indicators.",
  },
  screen: {
    label: 'Saved screen',
    help: 'Checked each evening. Tells you when a stock newly matches the screen.',
  },
};

export interface AlertDialogProps {
  /** Present when editing. The kind and target of an alert cannot change. */
  editing?: AlertDetail;
  initialTicker?: string;
  initialPresetId?: string;
  onClose: () => void;
  onCreated?: (created: AlertCreated) => void;
}

/**
 * Create or edit an alert.
 *
 * The condition builder is the screener's, moved to `components/`, fed by the
 * catalogue for the alert's kind: `GET /alerts/fields?kind=intraday` offers
 * `price` and no `close`, and `end_of_day` the reverse. Changing the kind
 * clears the rows, since a row naming `price` means nothing at the close.
 */
export function AlertDialog({
  editing,
  initialTicker,
  initialPresetId,
  onClose,
  onCreated,
}: AlertDialogProps) {
  const parsed = useMemo(
    () => (editing?.condition ? parseFilterTree(editing.condition) : null),
    [editing],
  );
  const nested = Boolean(editing?.condition) && parsed === null;

  const [name, setName] = useState(editing?.name ?? '');
  const [kind, setKind] = useState<AlertKind>(
    editing?.kind ?? (initialPresetId ? 'screen' : 'intraday'),
  );
  const [ticker, setTicker] = useState<string | null>(editing?.ticker ?? initialTicker ?? null);
  const [presetId, setPresetId] = useState<string>(editing?.preset_id ?? initialPresetId ?? '');
  const [rows, setRows] = useState<FilterRow[]>(parsed?.rows ?? []);
  const [combinator, setCombinator] = useState<GroupOperator>(parsed?.combinator ?? 'and');
  const [triggerMode, setTriggerMode] = useState<TriggerMode>(editing?.trigger_mode ?? 'once');
  const [cooldown, setCooldown] = useState(String(editing?.cooldown_minutes ?? 60));

  const conditionKind: ConditionKind | null = kind === 'screen' ? null : kind;
  const fields = useAlertFields(conditionKind);
  const presets = usePresetOptions(kind === 'screen');
  const create = useCreateAlert();
  const update = useUpdateAlert();
  const pending = create.isPending || update.isPending;
  const error = create.error ?? update.error;

  const tree = useMemo(() => buildFilterTree(rows, combinator), [rows, combinator]);
  const cooldownMinutes = Number(cooldown);
  const cooldownValid =
    Number.isInteger(cooldownMinutes) && cooldownMinutes >= 5 && cooldownMinutes <= 1440;
  const effectiveMode: TriggerMode = kind === 'screen' ? 'rearm' : triggerMode;

  const ready =
    name.trim().length > 0 &&
    (kind === 'screen' ? presetId !== '' : ticker !== null && (tree !== null || nested)) &&
    (effectiveMode === 'once' || kind === 'screen' || cooldownValid);

  function changeKind(next: AlertKind | null) {
    if (!next || next === kind) return;
    setKind(next);
    setRows([]);
    setCombinator('and');
  }

  function submit() {
    const cooldownPart =
      effectiveMode === 'rearm' && kind !== 'screen' ? { cooldown_minutes: cooldownMinutes } : {};

    if (editing) {
      update.mutate(
        {
          id: editing.id,
          patch: {
            name: name.trim(),
            ...(kind !== 'screen' ? { trigger_mode: effectiveMode } : {}),
            ...(tree && !nested ? { condition: tree } : {}),
            ...cooldownPart,
          },
        },
        { onSuccess: onClose },
      );
      return;
    }

    create.mutate(
      kind === 'screen'
        ? { name: name.trim(), kind, preset_id: presetId }
        : {
            name: name.trim(),
            kind,
            ticker: ticker!,
            condition: tree!,
            trigger_mode: effectiveMode,
            ...cooldownPart,
          },
      {
        onSuccess: (created) => {
          onCreated?.(created);
          onClose();
        },
      },
    );
  }

  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>{editing ? 'Edit alert' : 'New alert'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ pt: 1 }}>
          <TextField
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            slotProps={{ htmlInput: { maxLength: 80 } }}
            autoFocus
          />

          <Stack spacing={1}>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={kind}
              disabled={Boolean(editing)}
              onChange={(_, next: AlertKind | null) => changeKind(next)}
              aria-label="Alert kind"
            >
              {(Object.keys(KIND_LABELS) as AlertKind[]).map((value) => (
                <ToggleButton key={value} value={value}>
                  {KIND_LABELS[value].label}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            <Typography variant="body2" color="text.secondary">
              {KIND_LABELS[kind].help}
            </Typography>
          </Stack>

          {kind === 'screen' ? (
            <TextField
              select
              label="Saved screen"
              value={presetId}
              disabled={Boolean(editing)}
              onChange={(event) => setPresetId(event.target.value)}
              helperText={
                presets.data?.length === 0 ? 'Save a screen in the screener first.' : undefined
              }
            >
              {presets.data?.map((preset) => (
                <MenuItem key={preset.id} value={preset.id}>
                  {preset.name}
                </MenuItem>
              ))}
            </TextField>
          ) : ticker ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                Symbol
              </Typography>
              <Chip
                label={ticker}
                onDelete={editing ? undefined : () => setTicker(null)}
                aria-label={`Symbol ${ticker}`}
              />
            </Stack>
          ) : (
            <SymbolSearch label="Symbol" onSelect={(symbol) => setTicker(symbol.ticker)} />
          )}

          {conditionKind &&
            (nested ? (
              <Alert severity="info">
                This condition has nested groups, which this editor cannot show. It still runs; edit
                the other settings here, or replace it through the API.
              </Alert>
            ) : (
              <Stack spacing={1}>
                <Typography variant="subtitle2">Fire when</Typography>
                <FilterBuilder
                  fields={fields.data?.fields ?? []}
                  rows={rows}
                  combinator={combinator}
                  onRowsChange={setRows}
                  onCombinatorChange={setCombinator}
                  disabled={!fields.data}
                  emptyText="No conditions yet. Add one: the alert fires when it becomes true."
                />
              </Stack>
            ))}

          {kind !== 'screen' && (
            <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <ToggleButtonGroup
                exclusive
                size="small"
                value={triggerMode}
                onChange={(_, next: TriggerMode | null) => next && setTriggerMode(next)}
                aria-label="After it fires"
              >
                <ToggleButton value="once">Fire once</ToggleButton>
                <ToggleButton value="rearm">Fire every time</ToggleButton>
              </ToggleButtonGroup>
              {triggerMode === 'rearm' && (
                <TextField
                  size="small"
                  label="Quiet period (minutes)"
                  value={cooldown}
                  onChange={(event) => setCooldown(event.target.value)}
                  error={!cooldownValid}
                  helperText={cooldownValid ? 'No repeat within this' : '5 to 1440'}
                  slotProps={{ htmlInput: { inputMode: 'numeric' } }}
                  sx={{ width: 200 }}
                />
              )}
            </Stack>
          )}

          <Typography variant="body2" color="text.secondary">
            An alert fires when its condition <em>becomes</em> true, not while it stays true. A new
            alert never fires on its first check.
          </Typography>

          {error && (
            <Alert severity="error">
              {error instanceof ApiError ? error.message : 'Something went wrong.'}
            </Alert>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!ready} loading={pending} onClick={submit}>
          {editing ? 'Save' : 'Create alert'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
