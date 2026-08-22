/**
 * One symbol, charted.
 *
 * The composition point: it owns the window and indicator choice, fetches the
 * two series, hands the shaped result to the shared chart wrapper, and writes
 * the choice back to the user's preferences. Everything it renders below the
 * heading is a component; the page itself is state and wiring.
 */
import { useMemo, useState } from 'react';
import { Link as RouterLink, useParams } from 'react-router';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';
import Link from '@mui/material/Link';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useColorScheme } from '@mui/material/styles';
import { CandlestickChart } from '@/components/chart/CandlestickChart';
import { chartColors } from '@/components/chart/chartTheme';
import { ApiError } from '@/lib/api/client';
import { ChartControls } from '../components/ChartControls';
import { ChartReadout } from '../components/ChartReadout';
import { useChartPreferences, useSaveChartPreferences } from '../api/useChartPreferences';
import { useBars, useIndicators, useSymbol, type ChartRange } from '../api/useSymbolChart';
import { OVERLAY_SLOT, type OscillatorKey, type OverlayKey } from '../lib/indicators';
import { toChartSeries } from '../lib/toChartSeries';

interface Choice {
  range: ChartRange;
  overlays: OverlayKey[];
  oscillators: OscillatorKey[];
}

/** Used until preferences load. Replaced, not merged, once they arrive. */
const FALLBACK: Choice = { range: '1Y', overlays: ['sma_20', 'sma_50'], oscillators: ['rsi_14'] };

function errorMessage(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.message;
  return 'Something went wrong. Please try again.';
}

export function SymbolDetailPage() {
  const { ticker = '' } = useParams<{ ticker: string }>();
  const upperTicker = ticker.toUpperCase();

  const symbol = useSymbol(upperTicker);
  const preferences = useChartPreferences();
  const savePreferences = useSaveChartPreferences();

  // Only the user's explicit choice is state; what is actually shown is derived
  // during render from that choice, then their saved preferences, then the
  // fallback. Syncing preferences into state with an effect would give the
  // chart two sources of truth and an extra render pass — the Phase 2 lesson.
  const [chosen, setChosen] = useState<Choice | null>(null);
  const active: Choice = chosen ?? {
    range: preferences.data?.default_range ?? FALLBACK.range,
    overlays: preferences.data?.active_overlays ?? FALLBACK.overlays,
    oscillators: preferences.data?.active_oscillators ?? FALLBACK.oscillators,
  };

  const bars = useBars(upperTicker, active.range);
  const indicators = useIndicators(upperTicker, active.range);

  // `mode` can be 'system', in which case `systemMode` carries the resolved
  // one. Dark is the app's default scheme, so it is the fallback before the
  // provider has hydrated.
  const { mode, systemMode } = useColorScheme();
  const scheme = systemMode ?? (mode === 'system' ? undefined : mode) ?? 'dark';
  const colors = chartColors(scheme);

  const [hoveredTime, setHoveredTime] = useState<string | null>(null);

  const barRows = useMemo(() => bars.data?.bars ?? [], [bars.data]);
  const indicatorRows = useMemo(() => indicators.data?.indicators ?? [], [indicators.data]);

  const series = useMemo(
    () =>
      toChartSeries(
        barRows,
        indicatorRows,
        { overlays: active.overlays, oscillators: active.oscillators },
        colors.slots,
      ),
    [barRows, indicatorRows, active.overlays, active.oscillators, colors.slots],
  );

  const readoutBar =
    (hoveredTime ? barRows.find((bar) => bar.trade_date === hoveredTime) : undefined) ??
    barRows[barRows.length - 1];
  const readoutIndicator = readoutBar
    ? indicatorRows.find((row) => row.trade_date === readoutBar.trade_date)
    : undefined;

  /** Every control change writes the whole preference back — the API is a PUT. */
  function choose(next: Choice) {
    setChosen(next);
    savePreferences.mutate({
      default_range: next.range,
      active_overlays: next.overlays,
      active_oscillators: next.oscillators,
    });
  }

  if (symbol.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (symbol.isError) {
    const unknown = symbol.error instanceof ApiError && symbol.error.status === 404;
    return (
      <Alert severity={unknown ? 'warning' : 'error'} sx={{ mt: 2 }}>
        {unknown
          ? `No symbol called ${upperTicker}. Check the ticker, or search for it from a watchlist.`
          : errorMessage(symbol.error)}
      </Alert>
    );
  }

  const loadingSeries = bars.isPending || indicators.isPending;
  const seriesError = bars.error ?? indicators.error;

  return (
    <Stack sx={{ gap: 2, py: 2 }}>
      <Stack sx={{ flexDirection: 'row', alignItems: 'baseline', gap: 1.5, flexWrap: 'wrap' }}>
        <Typography variant="h1">{symbol.data.ticker}</Typography>
        <Typography variant="h3" color="text.secondary">
          {symbol.data.name}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Link component={RouterLink} to="/watchlists" variant="body2">
          Back to watchlists
        </Link>
      </Stack>

      {savePreferences.isError ? (
        <Alert severity="warning" onClose={() => savePreferences.reset()}>
          Your chart is showing what you chose, but it could not be saved
          {savePreferences.error instanceof ApiError
            ? `: ${savePreferences.error.message}`
            : '.'}{' '}
          It will be back to the previous settings after a reload.
        </Alert>
      ) : null}

      <Card>
        <CardContent>
          <Stack sx={{ gap: 2 }}>
            <ChartControls
              range={active.range}
              overlays={active.overlays}
              oscillators={active.oscillators}
              onRangeChange={(range) => choose({ ...active, range })}
              onOverlaysChange={(overlays) => choose({ ...active, overlays })}
              onOscillatorsChange={(oscillators) => choose({ ...active, oscillators })}
            />

            {seriesError ? (
              <Alert
                severity="error"
                action={
                  <Button
                    color="inherit"
                    size="small"
                    onClick={() => {
                      void bars.refetch();
                      void indicators.refetch();
                    }}
                  >
                    Retry
                  </Button>
                }
              >
                {errorMessage(seriesError)}
              </Alert>
            ) : null}

            {loadingSeries ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 10 }}>
                <CircularProgress />
              </Box>
            ) : null}

            {!loadingSeries && !seriesError && barRows.length === 0 ? (
              // The common case while the universe is seeded but only a few
              // symbols are backfilled — so say what to do about it.
              <Alert severity="info">
                No price history yet for {symbol.data.ticker}. Run{' '}
                <Box component="code">make backfill tickers={symbol.data.ticker}</Box> to fetch it.
              </Alert>
            ) : null}

            {!loadingSeries && barRows.length > 0 ? (
              <>
                <ChartReadout
                  bar={readoutBar}
                  indicator={readoutIndicator}
                  overlays={active.overlays}
                  oscillators={active.oscillators}
                  slotColors={colors.slots}
                  overlaySlot={(key) => OVERLAY_SLOT[key]}
                  hovered={hoveredTime !== null}
                />
                <CandlestickChart
                  data={series}
                  colors={colors}
                  onHoverTimeChange={setHoveredTime}
                  // The canvas is opaque to assistive technology, so the label
                  // says what it is and points at the readout above, which is
                  // real text and reports whichever bar the crosshair is on.
                  ariaLabel={`${symbol.data.ticker} candlestick chart with volume${
                    active.oscillators.length > 0 ? ' and indicator panes' : ''
                  }. Values for the selected bar are given above the chart.`}
                />
              </>
            ) : null}
          </Stack>
        </CardContent>
      </Card>
    </Stack>
  );
}
