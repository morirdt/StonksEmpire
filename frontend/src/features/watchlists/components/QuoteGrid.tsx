import { useMemo } from 'react';
import Box from '@mui/material/Box';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import DeleteIcon from '@mui/icons-material/Delete';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import { DataGrid, type GridColDef, type GridRenderCellParams } from '@mui/x-data-grid';
import {
  EM_DASH,
  changeTone,
  formatAge,
  formatChange,
  formatPercent,
  formatPrice,
  formatVolume,
  toNumber,
} from '@/lib/format';
import type { WatchlistRow } from '../api/useWatchlists';

interface QuoteGridProps {
  rows: WatchlistRow[];
  loading?: boolean;
  onRemove: (itemId: string) => void;
  onMove: (itemId: string, direction: -1 | 1) => void;
  busy?: boolean;
}

/** One flattened grid row. Prices stay strings until the cell renders them. */
interface GridRow {
  id: string;
  ticker: string;
  name: string;
  price: string | null;
  change: string | null;
  changePercent: string | null;
  volume: number | null;
  fetchedAt: string | null;
  position: number;
  isFirst: boolean;
  isLast: boolean;
}

/**
 * A cell whose colour carries meaning.
 *
 * `profit` / `loss` are palette tokens, never green/red literals — the theme
 * owns what those look like, including in light mode.
 */
function ChangeCell({
  value,
  format,
}: {
  value: string | null;
  format: (v: string | null) => string;
}) {
  const tone = changeTone(value);
  return (
    <Box
      component="span"
      sx={{
        color: tone === 'flat' ? 'text.secondary' : `${tone}.main`,
        fontVariantNumeric: 'tabular-nums',
        fontWeight: 600,
      }}
    >
      {format(value)}
    </Box>
  );
}

export function QuoteGrid({
  rows,
  loading = false,
  onRemove,
  onMove,
  busy = false,
}: QuoteGridProps) {
  const gridRows = useMemo<GridRow[]>(
    () =>
      rows.map((row, index) => ({
        id: row.item.id,
        ticker: row.symbol.ticker,
        name: row.symbol.name,
        price: row.quote?.price ?? null,
        change: row.quote?.change ?? null,
        changePercent: row.quote?.change_percent ?? null,
        volume: row.quote?.volume ?? null,
        fetchedAt: row.quote?.fetched_at ?? null,
        position: row.item.position,
        isFirst: index === 0,
        isLast: index === rows.length - 1,
      })),
    [rows],
  );

  const columns = useMemo<GridColDef<GridRow>[]>(
    () => [
      {
        field: 'ticker',
        headerName: 'Symbol',
        flex: 0.8,
        minWidth: 110,
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <Box
            sx={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', height: 1 }}
          >
            <Typography variant="body2" sx={{ fontWeight: 700 }}>
              {params.row.ticker}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {params.row.name}
            </Typography>
          </Box>
        ),
      },
      {
        field: 'price',
        headerName: 'Price',
        flex: 0.6,
        minWidth: 100,
        align: 'right',
        headerAlign: 'right',
        // Sort on the parsed number; display keeps the exact string.
        valueGetter: (_value, row) => toNumber(row.price),
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <Box component="span" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatPrice(params.row.price)}
          </Box>
        ),
      },
      {
        field: 'change',
        headerName: 'Change',
        flex: 0.6,
        minWidth: 100,
        align: 'right',
        headerAlign: 'right',
        valueGetter: (_value, row) => toNumber(row.change),
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <ChangeCell value={params.row.change} format={formatChange} />
        ),
      },
      {
        field: 'changePercent',
        headerName: 'Change %',
        flex: 0.6,
        minWidth: 110,
        align: 'right',
        headerAlign: 'right',
        valueGetter: (_value, row) => toNumber(row.changePercent),
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <ChangeCell value={params.row.changePercent} format={formatPercent} />
        ),
      },
      {
        field: 'volume',
        headerName: 'Volume',
        flex: 0.5,
        minWidth: 90,
        align: 'right',
        headerAlign: 'right',
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <Box component="span" sx={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatVolume(params.row.volume)}
          </Box>
        ),
      },
      {
        field: 'fetchedAt',
        headerName: 'As of',
        flex: 0.5,
        minWidth: 90,
        sortable: false,
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <Tooltip title={params.row.fetchedAt ?? 'Never fetched'}>
            <Typography variant="caption" color="text.secondary">
              {formatAge(params.row.fetchedAt)}
            </Typography>
          </Tooltip>
        ),
      },
      {
        field: 'actions',
        headerName: '',
        width: 130,
        sortable: false,
        filterable: false,
        disableColumnMenu: true,
        align: 'right',
        renderCell: (params: GridRenderCellParams<GridRow>) => (
          <Box>
            {/* Buttons rather than drag-and-drop: this is keyboard-reachable,
                and reordering by keyboard is not an accessibility afterthought
                on a screen whose whole purpose is ordering things. */}
            <IconButton
              size="small"
              aria-label={`Move ${params.row.ticker} up`}
              disabled={params.row.isFirst || busy}
              onClick={() => onMove(params.row.id, -1)}
            >
              <ArrowUpwardIcon fontSize="inherit" />
            </IconButton>
            <IconButton
              size="small"
              aria-label={`Move ${params.row.ticker} down`}
              disabled={params.row.isLast || busy}
              onClick={() => onMove(params.row.id, 1)}
            >
              <ArrowDownwardIcon fontSize="inherit" />
            </IconButton>
            <IconButton
              size="small"
              aria-label={`Remove ${params.row.ticker}`}
              disabled={busy}
              onClick={() => onRemove(params.row.id)}
            >
              <DeleteIcon fontSize="inherit" />
            </IconButton>
          </Box>
        ),
      },
    ],
    [busy, onMove, onRemove],
  );

  return (
    <DataGrid<GridRow>
      rows={gridRows}
      columns={columns}
      loading={loading}
      density="comfortable"
      disableRowSelectionOnClick
      hideFooter={gridRows.length <= 25}
      initialState={{ pagination: { paginationModel: { pageSize: 25 } } }}
      pageSizeOptions={[25, 50, 100]}
      localeText={{ noRowsLabel: `No symbols yet ${EM_DASH} add one above` }}
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        // Every numeral the same width, or the price column visibly wobbles
        // each time a digit changes on a poll.
        fontVariantNumeric: 'tabular-nums',
        '& .MuiDataGrid-cell:focus, & .MuiDataGrid-cell:focus-within': { outlineOffset: -2 },
      }}
      aria-label="Watchlist quotes"
    />
  );
}
