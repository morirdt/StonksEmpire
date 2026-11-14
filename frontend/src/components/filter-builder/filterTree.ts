/**
 * The builder's state, and the wire format, and the pure pair between them.
 *
 * The filter builder is a flat list of rows — one row per predicate, joined by
 * a single AND/OR — because that is the shape a person can actually operate.
 * The API takes a tree. These two functions are the whole conversion, kept pure
 * and tested directly; they are where this phase's frontend logic lives, the
 * same role `toChartSeries` plays for the chart.
 *
 * The asymmetry is deliberate and worth stating: `buildFilterTree` can express
 * every flat list as a tree, but `parseFilterTree` cannot express every tree as
 * a flat list. A nested screen — saved by a future builder, or written by hand
 * against the API — round-trips through the *server* perfectly well; it just
 * cannot be edited in this UI, so parsing returns `null` and the page says so
 * rather than silently flattening a screen into a different one.
 */
import type { components } from '@/lib/api/schema';

export type FilterNode = components['schemas']['ScreenerRunRequest']['filters'];
export type ScreenerField = components['schemas']['ScreenerFieldResponse'];
export type FilterKind = components['schemas']['FilterKind'];
export type NumericOperator = components['schemas']['NumericOperator'];
export type CompareOperator = components['schemas']['CompareOperator'];
export type CategoryOperator = components['schemas']['CategoryOperator'];
export type GroupOperator = components['schemas']['GroupOperator'];
export type ScreenerSort = components['schemas']['ScreenerSort'];

export interface NumericRow {
  id: string;
  kind: 'numeric';
  field: string;
  op: NumericOperator;
  /** Kept as typed text, never a number — see `lib/format.ts` for why. */
  value: string;
  value2: string;
}

export interface CompareRow {
  id: string;
  kind: 'compare';
  left: string;
  op: CompareOperator;
  right: string;
}

export interface CategoryRow {
  id: string;
  kind: 'category';
  field: string;
  op: CategoryOperator;
  values: string[];
}

export type FilterRow = NumericRow | CompareRow | CategoryRow;

/** Labels for every operator, in the order a dropdown should offer them. */
export const NUMERIC_OPERATORS: { value: NumericOperator; label: string }[] = [
  { value: 'gt', label: 'is above' },
  { value: 'gte', label: 'is at least' },
  { value: 'lt', label: 'is below' },
  { value: 'lte', label: 'is at most' },
  { value: 'eq', label: 'equals' },
  { value: 'neq', label: 'does not equal' },
  { value: 'between', label: 'is between' },
];

export const COMPARE_OPERATORS: { value: CompareOperator; label: string }[] =
  NUMERIC_OPERATORS.filter((o) => o.value !== 'between') as {
    value: CompareOperator;
    label: string;
  }[];

export const CATEGORY_OPERATORS: { value: CategoryOperator; label: string }[] = [
  { value: 'in', label: 'is one of' },
  { value: 'not_in', label: 'is not one of' },
];

let nextRowId = 0;

/** Row ids are local identity for React keys, and never leave the browser. */
export function newRowId(): string {
  nextRowId += 1;
  return `row-${nextRowId}`;
}

export function fieldsSupporting(fields: ScreenerField[], kind: FilterKind): ScreenerField[] {
  return fields.filter((field) => field.kinds.includes(kind));
}

/** A new row of the given kind, pre-filled with the first field that fits. */
export function emptyRow(kind: FilterKind, fields: ScreenerField[]): FilterRow | null {
  if (kind === 'numeric') {
    const field = fieldsSupporting(fields, 'numeric')[0];
    if (!field) return null;
    return { id: newRowId(), kind: 'numeric', field: field.key, op: 'gt', value: '', value2: '' };
  }
  if (kind === 'compare') {
    const candidates = fieldsSupporting(fields, 'compare');
    const left = candidates[0];
    const right = candidates.find((f) => f.unit === left?.unit && f.key !== left.key) ?? left;
    if (!left || !right) return null;
    return { id: newRowId(), kind: 'compare', left: left.key, op: 'gt', right: right.key };
  }
  if (kind === 'category') {
    const field = fieldsSupporting(fields, 'category')[0];
    if (!field) return null;
    return { id: newRowId(), kind: 'category', field: field.key, op: 'in', values: [] };
  }
  return null;
}

/**
 * Whether a row says something a query could act on.
 *
 * Incomplete rows are dropped rather than sent. A half-typed row is a row the
 * user has not finished, and shipping it would either 422 the whole screen or —
 * worse — quietly narrow the result to something nobody asked for.
 */
export function isComplete(row: FilterRow): boolean {
  if (row.kind === 'numeric') {
    if (!row.field || !isNumericText(row.value)) return false;
    return row.op !== 'between' || isNumericText(row.value2);
  }
  if (row.kind === 'compare') return Boolean(row.left && row.right);
  return row.values.length > 0;
}

function isNumericText(value: string): boolean {
  const trimmed = value.trim();
  return trimmed !== '' && Number.isFinite(Number(trimmed));
}

/**
 * The flat builder state as a filter tree, or `null` if nothing is runnable.
 *
 * Always a group, even for one row: the group carries the AND/OR the user
 * chose, and a single-row screen that later gains a second row must not change
 * shape in the process.
 */
export function buildFilterTree(rows: FilterRow[], combinator: GroupOperator): FilterNode | null {
  const children = rows.filter(isComplete).map(toNode);
  if (children.length === 0) return null;
  return { kind: 'group', op: combinator, children };
}

function toNode(row: FilterRow): FilterNode {
  if (row.kind === 'numeric') {
    // Values go out as trimmed strings. Sending a JSON number is a 422 by
    // design: it would be parsed as a float and lose the last cent.
    const base = {
      kind: 'numeric' as const,
      field: row.field,
      op: row.op,
      value: row.value.trim(),
    };
    return row.op === 'between' ? { ...base, value2: row.value2.trim() } : base;
  }
  if (row.kind === 'compare') {
    return { kind: 'compare', left: row.left, op: row.op, right: row.right };
  }
  return { kind: 'category', field: row.field, op: row.op, values: row.values };
}

/**
 * A tree back into builder rows, or `null` if this builder cannot express it.
 *
 * `null` means nested: the flat UI has no way to show a group inside a group,
 * and flattening one would change which symbols the screen returns.
 */
export function parseFilterTree(
  node: FilterNode | null | undefined,
): { combinator: GroupOperator; rows: FilterRow[] } | null {
  if (!node) return null;

  if (node.kind !== 'group') {
    const row = toRow(node);
    return row ? { combinator: 'and', rows: [row] } : null;
  }

  const rows: FilterRow[] = [];
  for (const child of node.children) {
    if (child.kind === 'group') return null;
    const row = toRow(child);
    if (!row) return null;
    rows.push(row);
  }
  return { combinator: node.op, rows };
}

function toRow(node: FilterNode): FilterRow | null {
  if (node.kind === 'numeric') {
    return {
      id: newRowId(),
      kind: 'numeric',
      field: node.field,
      op: node.op,
      value: String(node.value),
      value2: node.value2 === null || node.value2 === undefined ? '' : String(node.value2),
    };
  }
  if (node.kind === 'compare') {
    return { id: newRowId(), kind: 'compare', left: node.left, op: node.op, right: node.right };
  }
  if (node.kind === 'category') {
    return {
      id: newRowId(),
      kind: 'category',
      field: node.field,
      op: node.op,
      values: node.values,
    };
  }
  return null;
}

/**
 * A stable string for a run, so two identical screens share one cache entry.
 *
 * The limit is part of the identity, not a detail: `total_matched` is the
 * pre-cap count but the rows are capped, so the same tree at two limits is two
 * different answers and must not share an entry.
 */
export function filterTreeKey(tree: FilterNode | null, sort: ScreenerSort, limit: number): string {
  return JSON.stringify({ tree, sort, limit });
}
