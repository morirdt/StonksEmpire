/**
 * The range selector and the indicator toggles.
 *
 * The three-overlay cap is enforced here as well as in the API schema, and it
 * is enforced by *disabling* the unselected toggles rather than by silently
 * dropping the fourth: a control that accepts a click and does nothing is worse
 * than one that says why it cannot.
 */
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { CHART_RANGES, type ChartRange } from '../api/useSymbolChart';
import {
  MAX_ACTIVE_OVERLAYS,
  OSCILLATOR_KEYS,
  OSCILLATOR_LABEL,
  OVERLAY_KEYS,
  OVERLAY_LABEL,
  type OscillatorKey,
  type OverlayKey,
} from '../lib/indicators';

export interface ChartControlsProps {
  range: ChartRange;
  overlays: OverlayKey[];
  oscillators: OscillatorKey[];
  onRangeChange: (range: ChartRange) => void;
  onOverlaysChange: (overlays: OverlayKey[]) => void;
  onOscillatorsChange: (oscillators: OscillatorKey[]) => void;
}

function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

export function ChartControls({
  range,
  overlays,
  oscillators,
  onRangeChange,
  onOverlaysChange,
  onOscillatorsChange,
}: ChartControlsProps) {
  const atCap = overlays.length >= MAX_ACTIVE_OVERLAYS;

  return (
    <Stack
      sx={{
        flexDirection: { xs: 'column', md: 'row' },
        alignItems: { xs: 'flex-start', md: 'center' },
        justifyContent: 'space-between',
        gap: 2,
        flexWrap: 'wrap',
      }}
    >
      <ToggleButtonGroup
        size="small"
        exclusive
        value={range}
        aria-label="Chart range"
        onChange={(_event, next: ChartRange | null) => {
          // null is the click that would deselect the current range, leaving
          // the chart with no window at all.
          if (next) onRangeChange(next);
        }}
      >
        {CHART_RANGES.map((option) => (
          <ToggleButton key={option} value={option} aria-label={option}>
            {option}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      <Stack sx={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 1 }}>
        <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
          Overlays
        </Typography>
        {OVERLAY_KEYS.map((key) => {
          const selected = overlays.includes(key);
          const blocked = !selected && atCap;
          return (
            <Tooltip
              key={key}
              title={blocked ? `At most ${MAX_ACTIVE_OVERLAYS} overlays at once` : ''}
              disableHoverListener={!blocked}
            >
              {/* A disabled Chip fires no events, so the tooltip needs a live
                  wrapper to hang off. */}
              <Box component="span" sx={{ display: 'inline-flex' }}>
                <Chip
                  size="small"
                  label={OVERLAY_LABEL[key]}
                  variant={selected ? 'filled' : 'outlined'}
                  color={selected ? 'primary' : 'default'}
                  disabled={blocked}
                  role="button"
                  aria-pressed={selected}
                  onClick={() => onOverlaysChange(toggle(overlays, key))}
                />
              </Box>
            </Tooltip>
          );
        })}

        <Typography variant="caption" color="text.secondary" sx={{ ml: 1, mr: 0.5 }}>
          Panes
        </Typography>
        {OSCILLATOR_KEYS.map((key) => {
          const selected = oscillators.includes(key);
          return (
            <Chip
              key={key}
              size="small"
              label={OSCILLATOR_LABEL[key]}
              variant={selected ? 'filled' : 'outlined'}
              color={selected ? 'primary' : 'default'}
              aria-pressed={selected}
              role="button"
              onClick={() => onOscillatorsChange(toggle(oscillators, key))}
            />
          );
        })}
      </Stack>
    </Stack>
  );
}
