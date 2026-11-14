/**
 * The flat-rows ↔ tree conversion, tested directly.
 *
 * This is the phase's pure frontend logic, so it is tested here rather than
 * through the builder: every incomplete-row and nesting case is a single
 * function call, where the same coverage through the DOM would be a dozen
 * clicks per assertion.
 */
import { describe, expect, it } from 'vitest';
import {
  buildFilterTree,
  emptyRow,
  isComplete,
  newRowId,
  parseFilterTree,
  type CategoryRow,
  type CompareRow,
  type FilterRow,
  type NumericRow,
  type ScreenerField,
} from './filterTree';

const FIELDS: ScreenerField[] = [
  { key: 'close', label: 'Close', unit: 'price', kinds: ['compare', 'numeric'], values: [] },
  { key: 'sma_200', label: 'SMA 200', unit: 'price', kinds: ['compare', 'numeric'], values: [] },
  { key: 'rsi_14', label: 'RSI 14', unit: 'ratio', kinds: ['numeric'], values: [] },
  {
    key: 'exchange',
    label: 'Exchange',
    unit: 'text',
    kinds: ['category'],
    values: ['NASDAQ', 'NYSE'],
  },
  { key: 'ticker', label: 'Ticker', unit: 'text', kinds: [], values: [] },
];

function numeric(overrides: Partial<NumericRow> = {}): NumericRow {
  return {
    id: newRowId(),
    kind: 'numeric',
    field: 'close',
    op: 'gt',
    value: '100',
    value2: '',
    ...overrides,
  };
}

function compare(overrides: Partial<CompareRow> = {}): CompareRow {
  return {
    id: newRowId(),
    kind: 'compare',
    left: 'close',
    op: 'gt',
    right: 'sma_200',
    ...overrides,
  };
}

function category(overrides: Partial<CategoryRow> = {}): CategoryRow {
  return {
    id: newRowId(),
    kind: 'category',
    field: 'exchange',
    op: 'in',
    values: ['NYSE'],
    ...overrides,
  };
}

describe('buildFilterTree', () => {
  it('wraps even a single row in a group, so adding a second changes nothing', () => {
    expect(buildFilterTree([numeric()], 'and')).toEqual({
      kind: 'group',
      op: 'and',
      children: [{ kind: 'numeric', field: 'close', op: 'gt', value: '100' }],
    });
  });

  it('carries the chosen combinator', () => {
    const tree = buildFilterTree([numeric(), compare()], 'or');
    expect(tree).toMatchObject({ op: 'or' });
  });

  it('is null when there is nothing runnable', () => {
    expect(buildFilterTree([], 'and')).toBeNull();
    expect(buildFilterTree([numeric({ value: '' })], 'and')).toBeNull();
  });

  it('drops half-finished rows rather than sending them', () => {
    const tree = buildFilterTree([numeric(), numeric({ value: '  ' })], 'and');
    expect(tree).toMatchObject({ children: [{ field: 'close' }] });
  });

  it('sends values as trimmed strings, never as numbers', () => {
    const tree = buildFilterTree([numeric({ value: ' 100.10 ' })], 'and');
    const child = (tree as { children: { value: unknown }[] }).children[0]!;
    expect(child.value).toBe('100.10');
    expect(typeof child.value).toBe('string');
  });

  it('includes value2 only for between', () => {
    const between = buildFilterTree([numeric({ op: 'between', value: '30', value2: '70' })], 'and');
    expect(between).toMatchObject({ children: [{ value: '30', value2: '70' }] });

    const plain = buildFilterTree([numeric({ value2: '70' })], 'and');
    expect((plain as { children: Record<string, unknown>[] }).children[0]).not.toHaveProperty(
      'value2',
    );
  });

  it('keeps a compare row as two field names', () => {
    expect(buildFilterTree([compare()], 'and')).toMatchObject({
      children: [{ kind: 'compare', left: 'close', op: 'gt', right: 'sma_200' }],
    });
  });

  it('keeps a category row as its value list', () => {
    expect(buildFilterTree([category({ values: ['NYSE', 'NASDAQ'] })], 'and')).toMatchObject({
      children: [{ kind: 'category', field: 'exchange', op: 'in', values: ['NYSE', 'NASDAQ'] }],
    });
  });
});

describe('isComplete', () => {
  it.each<[string, FilterRow, boolean]>([
    ['a filled numeric row', numeric(), true],
    ['an empty value', numeric({ value: '' }), false],
    ['a non-numeric value', numeric({ value: 'abc' }), false],
    ['between without an upper bound', numeric({ op: 'between' }), false],
    ['between with both bounds', numeric({ op: 'between', value: '1', value2: '2' }), true],
    ['a compare row', compare(), true],
    ['a compare row missing a side', compare({ right: '' }), false],
    ['a category row with values', category(), true],
    ['a category row with none', category({ values: [] }), false],
  ])('%s', (_label, row, expected) => {
    expect(isComplete(row)).toBe(expected);
  });
});

describe('parseFilterTree', () => {
  it('round-trips a flat screen', () => {
    const rows = [numeric(), compare(), category()];
    const tree = buildFilterTree(rows, 'or');

    const parsed = parseFilterTree(tree);

    expect(parsed?.combinator).toBe('or');
    expect(buildFilterTree(parsed!.rows, parsed!.combinator)).toEqual(tree);
  });

  it('reads a bare leaf as a one-row screen', () => {
    const parsed = parseFilterTree({ kind: 'numeric', field: 'close', op: 'gt', value: '5' });

    expect(parsed?.rows).toHaveLength(1);
    expect(parsed?.combinator).toBe('and');
  });

  it('is null for a nested tree rather than flattening it', () => {
    // Flattening would change which symbols the screen returns, which is worse
    // than refusing to edit it.
    const nested = {
      kind: 'group' as const,
      op: 'and' as const,
      children: [
        { kind: 'numeric' as const, field: 'close', op: 'gt' as const, value: '1' },
        {
          kind: 'group' as const,
          op: 'or' as const,
          children: [{ kind: 'numeric' as const, field: 'close', op: 'lt' as const, value: '2' }],
        },
      ],
    };

    expect(parseFilterTree(nested)).toBeNull();
  });

  it('is null for nothing', () => {
    expect(parseFilterTree(null)).toBeNull();
    expect(parseFilterTree(undefined)).toBeNull();
  });

  it('normalises a stored value2 of null to empty text', () => {
    const parsed = parseFilterTree({
      kind: 'numeric',
      field: 'close',
      op: 'gt',
      value: '5',
      value2: null,
    });

    expect((parsed!.rows[0] as NumericRow).value2).toBe('');
  });
});

describe('emptyRow', () => {
  it('picks a field that supports the kind', () => {
    expect(emptyRow('numeric', FIELDS)).toMatchObject({ field: 'close' });
    expect(emptyRow('category', FIELDS)).toMatchObject({ field: 'exchange' });
  });

  it('picks two same-unit fields for a comparison', () => {
    // A mismatch is a 422 from the server, so the default must not be one.
    const row = emptyRow('compare', FIELDS) as CompareRow;
    const unitOf = (key: string) => FIELDS.find((f) => f.key === key)?.unit;

    expect(unitOf(row.left)).toBe(unitOf(row.right));
    expect(row.left).not.toBe(row.right);
  });

  it('is null when the catalogue offers nothing of that kind', () => {
    expect(emptyRow('numeric', [FIELDS[4]!])).toBeNull();
  });
});
