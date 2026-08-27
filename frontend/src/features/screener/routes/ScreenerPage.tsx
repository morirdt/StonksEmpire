import { useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';
import Divider from '@mui/material/Divider';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { ApiError } from '@/lib/api/client';
import { FilterBuilder } from '../components/FilterBuilder';
import { PresetControls } from '../components/PresetControls';
import { ResultsTable } from '../components/ResultsTable';
import {
  useCreatePreset,
  useDeletePreset,
  useRunPreset,
  useScreenerFields,
  useScreenerPreset,
  useScreenerPresets,
  useScreenerRun,
} from '../api/useScreener';
import {
  buildFilterTree,
  parseFilterTree,
  type FilterNode,
  type FilterRow,
  type GroupOperator,
  type ScreenerSort,
} from '../lib/filterTree';

const DEFAULT_SORT: ScreenerSort = { field: 'ticker', direction: 'asc' };
const ROW_LIMIT = 100;

/**
 * What is currently running, as one piece of state.
 *
 * A screen runs from a saved preset **or** from the builder, never ambiguously
 * both — and running a preset goes through `/presets/{id}/run` rather than
 * posting the tree back, so what runs is what is stored under that name.
 * Editing the builder does not re-run anything; that is what the button is for,
 * or every keystroke would be a query.
 */
type RunMode =
  { kind: 'idle' } | { kind: 'preset'; id: string } | { kind: 'adhoc'; tree: FilterNode };

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.message;
  return 'Something went wrong. Please try again.';
}

export function ScreenerPage() {
  const fields = useScreenerFields();
  const presets = useScreenerPresets();
  const createPreset = useCreatePreset();
  const deletePreset = useDeletePreset();

  const [rows, setRows] = useState<FilterRow[]>([]);
  const [combinator, setCombinator] = useState<GroupOperator>('and');
  const [sort, setSort] = useState<ScreenerSort>(DEFAULT_SORT);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [mode, setMode] = useState<RunMode>({ kind: 'idle' });
  const [nested, setNested] = useState(false);

  const preset = useScreenerPreset(selectedPresetId);
  const presetRun = useRunPreset(mode.kind === 'preset' ? mode.id : null, ROW_LIMIT);
  const adhocRun = useScreenerRun(mode.kind === 'adhoc' ? mode.tree : null, sort, ROW_LIMIT);
  const run = mode.kind === 'preset' ? presetRun : adhocRun;

  // Loading a saved screen fills the builder from what was stored. A tree the
  // flat builder cannot express still runs — it just cannot be edited here, and
  // saying so beats flattening it into a different screen.
  //
  // Adjusted during render rather than in an effect, which is React's own
  // recommendation for "reset state when an input changes": an effect would
  // paint the previous screen's filters for one frame first, and would leave
  // two sources of truth for which preset the builder is showing.
  const loadedPreset = preset.data;
  const [builtFromPresetId, setBuiltFromPresetId] = useState<string | null>(null);
  if (loadedPreset && loadedPreset.id !== builtFromPresetId) {
    const parsed = parseFilterTree(loadedPreset.filters);
    setBuiltFromPresetId(loadedPreset.id);
    setNested(parsed === null);
    if (parsed) {
      setRows(parsed.rows);
      setCombinator(parsed.combinator);
    }
    setSort(loadedPreset.sort);
    setMode({ kind: 'preset', id: loadedPreset.id });
  }

  const draftTree = useMemo(() => buildFilterTree(rows, combinator), [rows, combinator]);
  const catalogue = fields.data?.fields ?? [];
  const results = run.data;

  const selectPreset = (id: string | null) => {
    setSelectedPresetId(id);
    if (id === null) {
      setBuiltFromPresetId(null);
      setRows([]);
      setCombinator('and');
      setSort(DEFAULT_SORT);
      setNested(false);
      setMode({ kind: 'idle' });
    }
  };

  /**
   * Sorting is a server round trip, because the response is capped: re-ordering
   * the hundred rows already returned would sort a hundred of four hundred
   * matches and present them as the top hundred.
   *
   * `/presets/{id}/run` has no sort parameter — what is saved under a name
   * includes its order — so re-sorting a saved screen runs its stored tree
   * ad-hoc instead. That works for a nested preset too, where the builder has
   * no rows to rebuild from.
   */
  const changeSort = (next: ScreenerSort) => {
    setSort(next);
    if (mode.kind === 'preset' && loadedPreset) {
      setMode({ kind: 'adhoc', tree: loadedPreset.filters });
    }
  };

  const runScreen = () => {
    if (!draftTree) return;
    setMode({ kind: 'adhoc', tree: draftTree });
  };

  const save = (name: string) => {
    if (!draftTree) return;
    createPreset.mutate(
      { name, filters: draftTree, sort },
      { onSuccess: (created) => setSelectedPresetId(created.id) },
    );
  };

  const remove = (id: string) => {
    deletePreset.mutate(id, { onSuccess: () => selectPreset(null) });
  };

  if (fields.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (fields.isError) {
    return <Alert severity="error">{errorMessage(fields.error)}</Alert>;
  }

  return (
    <Stack spacing={3}>
      <Stack
        direction="row"
        spacing={2}
        sx={{ alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}
      >
        <Typography variant="h1">Screener</Typography>
        <PresetControls
          presets={presets.data ?? []}
          selectedId={selectedPresetId}
          onSelect={selectPreset}
          onSave={save}
          onDelete={remove}
          saving={createPreset.isPending}
          canSave={draftTree !== null}
        />
      </Stack>

      {createPreset.isError && <Alert severity="error">{errorMessage(createPreset.error)}</Alert>}
      {preset.isError && <Alert severity="warning">{errorMessage(preset.error)}</Alert>}
      {nested && (
        <Alert severity="info">
          This screen has nested groups, which the builder cannot show. It still runs exactly as
          saved.
        </Alert>
      )}

      <Card>
        <CardContent>
          <FilterBuilder
            fields={catalogue}
            rows={rows}
            combinator={combinator}
            onRowsChange={setRows}
            onCombinatorChange={setCombinator}
            disabled={nested}
          />
          <Divider sx={{ my: 2 }} />
          <Button
            variant="contained"
            onClick={runScreen}
            disabled={!draftTree}
            loading={run.isFetching}
          >
            Run screen
          </Button>
        </CardContent>
      </Card>

      {run.isError && <Alert severity="error">{errorMessage(run.error)}</Alert>}

      {results && <ResultsSummary results={results} />}

      {results && results.universe_size === 0 && (
        <Alert severity="warning">
          The universe is empty — no symbol has price history for any date yet. Run{' '}
          <code>make backfill days=730</code> to load it.
        </Alert>
      )}

      {results && results.universe_size > 0 && results.rows.length === 0 && (
        <Typography color="text.secondary">
          No symbol matched. Try loosening a filter — {results.universe_size} symbols were
          considered.
        </Typography>
      )}

      {results && results.rows.length > 0 && (
        <Box sx={{ height: 560 }}>
          <ResultsTable
            columns={results.columns}
            rows={results.rows}
            sort={sort}
            onSortChange={changeSort}
            loading={run.isFetching}
          />
        </Box>
      )}
    </Stack>
  );
}

/**
 * The counts, spelled out.
 *
 * `total_matched` is the count before the row cap, so "412 matches, showing
 * 100" is the signal that a screen is too loose — which is why there is no
 * second page to click through to. `universe_size` is what makes a symbol
 * dropped for want of an indicator visible rather than silent.
 */
function ResultsSummary({
  results,
}: {
  results: { as_of: string | null; universe_size: number; total_matched: number; rows: unknown[] };
}) {
  const capped = results.total_matched > results.rows.length;
  return (
    <Typography variant="body2" color="text.secondary">
      {capped
        ? `${results.total_matched} matches, showing ${results.rows.length}`
        : `${results.total_matched} ${results.total_matched === 1 ? 'match' : 'matches'}`}
      {` · ${results.universe_size} symbols considered`}
      {results.as_of ? ` · as of ${results.as_of}` : ''}
    </Typography>
  );
}
