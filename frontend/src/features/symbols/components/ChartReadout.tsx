/**
 * The crosshair readout and the legend, in HTML rather than on the canvas.
 *
 * Two things the spec asks for, in one strip, because they answer the same
 * question — *what am I looking at?* — and splitting them would put the colour
 * key and the value it explains in different places.
 *
 * Rendered as DOM on purpose. Text painted into a canvas is unselectable,
 * invisible to a screen reader, and blurry on a scaled display; the crosshair
 * only has to report *which* bar is hovered for this to work.
 *
 * A chart of 500 candles cannot be read to a precise value without it, so it is
 * always present — falling back to the most recent bar when nothing is hovered,
 * rather than showing an empty strip that only fills in on hover.
 */
import Box from '@mui/material/Box';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { EM_DASH, changeTone, formatPrice, formatVolume, toNumber } from '@/lib/format';
import type { Bar, Indicator } from '../api/useSymbolChart';
import {
  OSCILLATOR_LABEL,
  OVERLAY_LABEL,
  type OscillatorKey,
  type OverlayKey,
} from '../lib/indicators';

export interface ChartReadoutProps {
  bar: Bar | undefined;
  indicator: Indicator | undefined;
  overlays: OverlayKey[];
  oscillators: OscillatorKey[];
  /** Slot colours, so the legend swatch matches the line it names. */
  slotColors: readonly string[];
  overlaySlot: (key: OverlayKey) => number;
  hovered: boolean;
}

function Field({ label, value, tone }: { label: string; value: string; tone?: 'profit' | 'loss' }) {
  return (
    <Stack sx={{ flexDirection: 'row', alignItems: 'baseline', gap: 0.5 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{ fontWeight: 600, color: tone ? `${tone}.main` : 'text.primary' }}
      >
        {value}
      </Typography>
    </Stack>
  );
}

/**
 * A colour swatch beside a name — never a name coloured *in* the series colour.
 * Labels wear text tokens; the mark beside them carries the identity.
 */
function Swatch({ color }: { color: string }) {
  return (
    <Box
      component="span"
      aria-hidden
      sx={{ width: 12, height: 2, borderRadius: 1, bgcolor: color, flexShrink: 0 }}
    />
  );
}

export function ChartReadout({
  bar,
  indicator,
  overlays,
  oscillators,
  slotColors,
  overlaySlot,
  hovered,
}: ChartReadoutProps) {
  if (!bar) return null;

  const direction = changeTone((toNumber(bar.close) ?? 0) - (toNumber(bar.open) ?? 0));

  return (
    <Stack
      // aria-live so a keyboard user moving the crosshair is told what changed;
      // polite rather than assertive, since it updates on every pixel of hover.
      aria-live="polite"
      sx={{
        flexDirection: 'row',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: { xs: 1.25, sm: 2 },
        px: 1.5,
        py: 1,
        borderRadius: 1,
        bgcolor: 'action.hover',
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 700 }}>
        {bar.trade_date}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {hovered ? 'at crosshair' : 'latest'}
      </Typography>

      <Field label="O" value={formatPrice(bar.open)} />
      <Field label="H" value={formatPrice(bar.high)} />
      <Field label="L" value={formatPrice(bar.low)} />
      <Field
        label="C"
        value={formatPrice(bar.close)}
        tone={direction === 'flat' ? undefined : direction}
      />
      <Field label="Vol" value={formatVolume(bar.volume)} />

      {overlays.map((key) => (
        <Stack key={key} sx={{ flexDirection: 'row', alignItems: 'center', gap: 0.75 }}>
          <Swatch color={slotColors[overlaySlot(key)] ?? 'text.secondary'} />
          <Field label={OVERLAY_LABEL[key]} value={formatPrice(indicator?.[key] ?? null)} />
        </Stack>
      ))}

      {oscillators.map((key) =>
        key === 'rsi_14' ? (
          <Field
            key={key}
            label={OSCILLATOR_LABEL.rsi_14}
            value={formatPrice(indicator?.rsi_14 ?? null)}
          />
        ) : (
          <Stack key={key} sx={{ flexDirection: 'row', alignItems: 'center', gap: 0.75 }}>
            <Swatch color={slotColors[0] ?? 'text.secondary'} />
            <Field label="MACD" value={formatPrice(indicator?.macd ?? null)} />
            <Swatch color={slotColors[1] ?? 'text.secondary'} />
            <Field label="Signal" value={formatPrice(indicator?.macd_signal ?? null)} />
          </Stack>
        ),
      )}

      {overlays.length === 0 && oscillators.length === 0 ? (
        <Typography variant="caption" color="text.secondary">
          {EM_DASH} no indicators shown
        </Typography>
      ) : null}
    </Stack>
  );
}
