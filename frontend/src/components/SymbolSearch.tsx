import { useState } from 'react';
import Autocomplete from '@mui/material/Autocomplete';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useSymbolSearch, type Symbol } from './useSymbolSearch';

interface SymbolSearchProps {
  onSelect: (symbol: Symbol) => void;
  label?: string;
  disabled?: boolean;
  /** Tickers already on the list, shown as disabled options rather than hidden. */
  excludeTickers?: string[];
}

/**
 * Type-ahead over the symbol universe.
 *
 * Lives in `components/` rather than in a feature because more than one feature
 * wants it — watchlists today, and the rule is that features may not import
 * each other, so shared code moves up rather than getting reached across for.
 * `useSymbolSearch` comes with it: the hook has no other consumer, so it is an
 * implementation detail of this control rather than part of any feature's API.
 *
 * Tickers already on the list are shown greyed out rather than filtered away:
 * a symbol vanishing from search results reads as "not found", which is a
 * worse answer than "already there".
 */
export function SymbolSearch({
  onSelect,
  label = 'Add a symbol',
  disabled = false,
  excludeTickers = [],
}: SymbolSearchProps) {
  const [inputValue, setInputValue] = useState('');
  const { data: options = [], isFetching } = useSymbolSearch(inputValue);
  const excluded = new Set(excludeTickers);

  return (
    <Autocomplete<Symbol>
      options={options}
      value={null}
      inputValue={inputValue}
      disabled={disabled}
      onInputChange={(_, value) => setInputValue(value)}
      onChange={(_, symbol) => {
        if (!symbol) return;
        onSelect(symbol);
        setInputValue('');
      }}
      getOptionLabel={(option) => option.ticker}
      getOptionDisabled={(option) => excluded.has(option.ticker)}
      isOptionEqualToValue={(option, value) => option.id === value.id}
      // The server already ranks these — exact ticker first. Re-filtering on
      // the client would undo that ordering.
      filterOptions={(all) => all}
      noOptionsText={inputValue.trim() ? 'No matching symbols' : 'Start typing a ticker or name'}
      loading={isFetching}
      renderOption={(props, option) => {
        const { key, ...rest } = props as typeof props & { key: string };
        return (
          <Box component="li" key={key} {...rest}>
            <Box sx={{ display: 'flex', flexDirection: 'column' }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {excluded.has(option.ticker) ? `${option.ticker} · already added` : option.ticker}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {option.exchange ? `${option.name} · ${option.exchange}` : option.name}
              </Typography>
            </Box>
          </Box>
        );
      }}
      renderInput={(params) => (
        <TextField
          {...params}
          label={label}
          size="small"
          slotProps={{
            // Spread the whole set, not just `input`: `htmlInput` carries the
            // real <input>'s props and ref, and replacing slotProps wholesale
            // leaves the Autocomplete unable to find its own input.
            ...params.slotProps,
            input: {
              ...params.slotProps.input,
              endAdornment: (
                <>
                  {isFetching ? <CircularProgress size={16} /> : null}
                  {params.slotProps.input.endAdornment}
                </>
              ),
            },
          }}
        />
      )}
    />
  );
}
