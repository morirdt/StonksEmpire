import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import DeleteIcon from '@mui/icons-material/Delete';
import {
  CATEGORY_OPERATORS,
  COMPARE_OPERATORS,
  NUMERIC_OPERATORS,
  emptyRow,
  fieldsSupporting,
  type FilterRow,
  type GroupOperator,
  type ScreenerField,
} from '../lib/filterTree';

interface FilterBuilderProps {
  fields: ScreenerField[];
  rows: FilterRow[];
  combinator: GroupOperator;
  onRowsChange: (rows: FilterRow[]) => void;
  onCombinatorChange: (op: GroupOperator) => void;
  disabled?: boolean;
}

/**
 * A field dropdown, built from the catalogue.
 *
 * There is no field list in this file. Everything offered here comes from
 * `GET /screener/fields`, which is the same registry the compiler resolves
 * against — so the builder cannot offer something the server will reject, and
 * a new column appears here without a frontend change.
 */
function FieldSelect({
  value,
  options,
  onChange,
  disabled,
  label,
}: {
  value: string;
  options: ScreenerField[];
  onChange: (key: string) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <Select
      size="small"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      inputProps={{ 'aria-label': label }}
      sx={{ minWidth: 190 }}
    >
      {options.map((field) => (
        <MenuItem key={field.key} value={field.key}>
          {field.label}
        </MenuItem>
      ))}
    </Select>
  );
}

function OperatorSelect<T extends string>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (op: T) => void;
  disabled?: boolean;
}) {
  return (
    <Select
      size="small"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as T)}
      inputProps={{ 'aria-label': 'Operator' }}
      sx={{ minWidth: 150 }}
    >
      {options.map((option) => (
        <MenuItem key={option.value} value={option.value}>
          {option.label}
        </MenuItem>
      ))}
    </Select>
  );
}

/**
 * One predicate.
 *
 * Numeric values are held and sent as text. They are never parsed into a
 * JavaScript number on the way out — the API rejects a JSON number precisely so
 * that `100.10` cannot become `100.09999999999999` in transit.
 */
function FilterRowEditor({
  row,
  fields,
  onChange,
  onRemove,
  disabled,
}: {
  row: FilterRow;
  fields: ScreenerField[];
  onChange: (row: FilterRow) => void;
  onRemove: () => void;
  disabled?: boolean;
}) {
  const numericFields = fieldsSupporting(fields, 'numeric');
  const compareFields = fieldsSupporting(fields, 'compare');
  const categoryFields = fieldsSupporting(fields, 'category');

  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
      {row.kind === 'numeric' && (
        <>
          <FieldSelect
            label="Field"
            value={row.field}
            options={numericFields}
            disabled={disabled}
            onChange={(field) => onChange({ ...row, field })}
          />
          <OperatorSelect
            value={row.op}
            options={NUMERIC_OPERATORS}
            disabled={disabled}
            onChange={(op) => onChange({ ...row, op })}
          />
          <TextField
            size="small"
            value={row.value}
            disabled={disabled}
            onChange={(event) => onChange({ ...row, value: event.target.value })}
            slotProps={{ htmlInput: { 'aria-label': 'Value', inputMode: 'decimal' } }}
            sx={{ width: 120 }}
          />
          {row.op === 'between' && (
            <>
              <Typography color="text.secondary">and</Typography>
              <TextField
                size="small"
                value={row.value2}
                disabled={disabled}
                onChange={(event) => onChange({ ...row, value2: event.target.value })}
                slotProps={{ htmlInput: { 'aria-label': 'Upper value', inputMode: 'decimal' } }}
                sx={{ width: 120 }}
              />
            </>
          )}
        </>
      )}

      {row.kind === 'compare' && (
        <>
          <FieldSelect
            label="Left field"
            value={row.left}
            options={compareFields}
            disabled={disabled}
            onChange={(left) => onChange({ ...row, left })}
          />
          <OperatorSelect
            value={row.op}
            options={COMPARE_OPERATORS}
            disabled={disabled}
            onChange={(op) => onChange({ ...row, op })}
          />
          {/* Only same-unit fields are offered: comparing a price with a
              share count is a 422, and a dropdown that can produce one is a
              dropdown that will. */}
          <FieldSelect
            label="Right field"
            value={row.right}
            options={compareFields.filter(
              (field) => field.unit === compareFields.find((f) => f.key === row.left)?.unit,
            )}
            disabled={disabled}
            onChange={(right) => onChange({ ...row, right })}
          />
        </>
      )}

      {row.kind === 'category' && (
        <>
          <FieldSelect
            label="Field"
            value={row.field}
            options={categoryFields}
            disabled={disabled}
            onChange={(field) => onChange({ ...row, field, values: [] })}
          />
          <OperatorSelect
            value={row.op}
            options={CATEGORY_OPERATORS}
            disabled={disabled}
            onChange={(op) => onChange({ ...row, op })}
          />
          <Select
            multiple
            size="small"
            value={row.values}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...row,
                values:
                  typeof event.target.value === 'string'
                    ? event.target.value.split(',')
                    : event.target.value,
              })
            }
            inputProps={{ 'aria-label': 'Values' }}
            renderValue={(selected) => (
              <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap' }}>
                {selected.map((value) => (
                  <Chip key={value} label={value} size="small" />
                ))}
              </Stack>
            )}
            sx={{ minWidth: 240 }}
          >
            {(categoryFields.find((f) => f.key === row.field)?.values ?? []).map((value) => (
              <MenuItem key={value} value={value}>
                {value}
              </MenuItem>
            ))}
          </Select>
        </>
      )}

      <IconButton
        size="small"
        onClick={onRemove}
        disabled={disabled}
        aria-label="Remove this filter"
      >
        <DeleteIcon fontSize="small" />
      </IconButton>
    </Stack>
  );
}

/**
 * The filter builder: a flat list of predicates joined by one AND or OR.
 *
 * Flat on purpose. Nesting is expressible in the API and unbuildable here, and
 * a builder that grows a tree editor before anyone has asked for nesting is a
 * builder nobody can use. `lib/filterTree.ts` converts between this shape and
 * the wire format, and says so when a saved screen is too nested to edit.
 */
export function FilterBuilder({
  fields,
  rows,
  combinator,
  onRowsChange,
  onCombinatorChange,
  disabled,
}: FilterBuilderProps) {
  const add = (kind: 'numeric' | 'compare' | 'category') => {
    const row = emptyRow(kind, fields);
    if (row) onRowsChange([...rows, row]);
  };

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
        <Typography variant="body2" color="text.secondary">
          Match
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={combinator}
          disabled={disabled}
          onChange={(_event, value: GroupOperator | null) => value && onCombinatorChange(value)}
          aria-label="How filters combine"
        >
          <ToggleButton value="and">all filters</ToggleButton>
          <ToggleButton value="or">any filter</ToggleButton>
        </ToggleButtonGroup>
      </Stack>

      {rows.length === 0 ? (
        <Typography color="text.secondary">
          {/* The first of the three empty states, and the only one the page
              can show before a request has been made. The other two —
              "nothing matched" and "the universe is empty" — depend on a
              response, and collapsing any pair of them into one sentence
              would hide the one whose fix is a command rather than a
              looser filter. */}
          No filters yet. Add one to run a screen over the universe.
        </Typography>
      ) : (
        <Stack spacing={1.5}>
          {rows.map((row) => (
            <FilterRowEditor
              key={row.id}
              row={row}
              fields={fields}
              disabled={disabled}
              onChange={(next) => onRowsChange(rows.map((r) => (r.id === row.id ? next : r)))}
              onRemove={() => onRowsChange(rows.filter((r) => r.id !== row.id))}
            />
          ))}
        </Stack>
      )}

      <Box>
        <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
          <Button size="small" onClick={() => add('numeric')} disabled={disabled}>
            Add value filter
          </Button>
          <Button size="small" onClick={() => add('compare')} disabled={disabled}>
            Add field comparison
          </Button>
          <Button size="small" onClick={() => add('category')} disabled={disabled}>
            Add category filter
          </Button>
        </Stack>
      </Box>
    </Stack>
  );
}
