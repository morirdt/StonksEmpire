import type { AlertSummary } from '../api/useAlerts';

type Labelled = Pick<AlertSummary, 'kind' | 'ticker' | 'preset_name' | 'condition_summary'>;

/**
 * Alerts have no name of their own: one is identified by what it watches.
 * Mirrors `symbol_label` on the backend, which titles notifications the same way.
 */
export function alertLabel(alert: Labelled): string {
  if (alert.kind === 'screen')
    return `New matches for “${alert.preset_name ?? 'a deleted screen'}”`;
  const ticker = alert.ticker ?? 'Unknown symbol';
  return alert.condition_summary ? `${ticker}: ${alert.condition_summary}` : ticker;
}
