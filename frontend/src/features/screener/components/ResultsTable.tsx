import { useMemo } from 'react';
import { Link as RouterLink } from 'react-router';
import Box from '@mui/material/Box';
import Link from '@mui/material/Link';
import { DataGrid, type GridColDef, type GridSortModel } from '@mui/x-data-grid';
import {
  EM_DASH,
  changeTone,
  formatPercent,
  formatPrice,
  formatVolume,
  toNumber,
} from '@/lib/format';
import type { ScreenerColumn, ScreenerRow } from '../api/useScreener';
import type { ScreenerSort } from '../lib/filterTree';

/**
 * One flattened grid row: the identity columns, then a value per referenced
 * field. Values stay strings until the cell renders them.
 */
interface ResultGridRow {
  id: string;
  ticker: string;
  name: string;
  exchange: string;
  [key: string]: string | null;
}

interface ResultsTableProps {
  columns: ScreenerColumn[];
  rows: ScreenerRow[];
  sort: ScreenerSort;
  onSortChange: (sort: ScreenerSort) => void;
  loading?: boolean;
}

/**
 * Render one value according to its unit.
 *
 * Values arrive as strings and are parsed here, at the point of display, and
 * nowhere else — see `lib/format.ts`. A ratio (RSI, 0-100) borrows the price
 * formatter because two decimals is what it wants too; it is not a price.
 */
function formatByUnit(unit: ScreenerColumn['unit'], value: string | null): string {
  if (value === null) return EM_DASH;
  if (unit === 'shares') return formatVolume(toNumber(value));
  if (unit === 'percent') return formatPercent(value);
  return formatPrice(value);
}

/** Percentages carry a sign as well as a colour — hue alone is not a signal. */
function ValueCell({ unit, value }: { unit: ScreenerColumn['unit']; value: string | null }) {
  const signed = unit === 'percent';
  const tone = signed ? changeTone(value) : 'flat';
  return (
    <Box
      component="span"
      sx={{
        fontVariantNumeric: 'tabular-nums',
        color: tone === 'flat' ? 'text.primary' : `${tone}.main`,
        fontWeight: signed ? 600 : 400,
      }}
    >
      {formatByUnit(unit, value)}
    </Box>
  );
}

/**
 * The matches, with the numbers that caused them.
 *
 * Sorting is **server-side**. The response is capped, so re-ordering the rows
 * already returned would sort a hundred of four hundred matches and present the
 * result as the top hundred — which is a different, wrong answer.
 *
 * Each ticker links to its chart. A link, not an import: `features/screener`
 * must not reach into `features/symbols`.
 */
export function ResultsTable({ columns, rows, sort, onSortChange, loading }: ResultsTableProps) {
  const gridColumns = useMemo<GridColDef<ResultGridRow>[]>(
    () => [
      {
        field: 'ticker',
        headerName: 'Ticker',
        width: 110,
        renderCell: (params) => (
          <Link component={RouterLink} to={`/symbols/${params.row.ticker}`} underline="hover">
            {params.row.ticker}
          </Link>
        ),
      },
      { field: 'name', headerName: 'Name', flex: 1, minWidth: 180 },
      { field: 'exchange', headerName: 'Exchange', width: 110 },
      ...columns.map<GridColDef<ResultGridRow>>((column) => ({
        field: column.key,
        headerName: column.label,
        width: 150,
        align: 'right',
        headerAlign: 'right',
        renderCell: (params) => (
          <ValueCell unit={column.unit} value={params.row[column.key] ?? null} />
        ),
      })),
    ],
    [columns],
  );

  const gridRows = useMemo<ResultGridRow[]>(
    () =>
      rows.map((row) => ({
        id: row.ticker,
        ticker: row.ticker,
        name: row.name,
        exchange: row.exchange ?? EM_DASH,
        ...row.values,
      })),
    [rows],
  );

  const sortModel: GridSortModel = [{ field: sort.field, sort: sort.direction }];

  return (
    <DataGrid
      rows={gridRows}
      columns={gridColumns}
      loading={loading}
      density="compact"
      disableColumnMenu
      disableRowSelectionOnClick
      hideFooter
      sortingMode="server"
      sortModel={sortModel}
      onSortModelChange={(model) => {
        const next = model[0];
        // Clearing the sort in the UI means "back to the default", not "no
        // order at all" — the API always returns rows in some sequence.
        onSortChange(
          next?.sort
            ? { field: next.field, direction: next.sort }
            : { field: 'ticker', direction: 'asc' },
        );
      }}
      sx={{ '& .MuiDataGrid-cell:focus': { outline: 'none' } }}
    />
  );
}
