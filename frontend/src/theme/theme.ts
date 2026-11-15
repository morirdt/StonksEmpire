import { createTheme } from '@mui/material/styles';
import { brand, market } from './palette';

declare module '@mui/material/styles' {
  interface Palette {
    profit: Palette['primary'];
    loss: Palette['primary'];
  }
  interface PaletteOptions {
    profit?: PaletteOptions['primary'];
    loss?: PaletteOptions['primary'];
  }
}

const FONT_STACK = [
  '"Inter"',
  '-apple-system',
  'BlinkMacSystemFont',
  '"Segoe UI"',
  'Roboto',
  'sans-serif',
].join(',');

export const theme = createTheme({
  /**
   * CSS variables mean the colour scheme switches without a React re-render
   * and without a flash of the wrong theme on first paint.
   */
  cssVariables: {
    colorSchemeSelector: 'class',
  },
  defaultColorScheme: 'dark',
  colorSchemes: {
    dark: {
      palette: {
        mode: 'dark',
        primary: { main: brand[300], light: brand[200], dark: brand[500] },
        background: { default: '#0b0e14', paper: '#141922' },
        divider: 'rgba(255,255,255,0.09)',
        profit: { main: market.profitMain, dark: market.profitDark, contrastText: '#fff' },
        loss: { main: market.lossMain, dark: market.lossDark, contrastText: '#fff' },
      },
    },
    light: {
      palette: {
        mode: 'light',
        primary: { main: brand[500], light: brand[300], dark: brand[700] },
        background: { default: '#f6f8fb', paper: '#ffffff' },
        profit: { main: market.profitDark, dark: market.profitDark, contrastText: '#fff' },
        loss: { main: market.lossDark, dark: market.lossDark, contrastText: '#fff' },
      },
    },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: FONT_STACK,
    h1: { fontSize: '2rem', fontWeight: 700, letterSpacing: '-0.02em' },
    h2: { fontSize: '1.5rem', fontWeight: 700, letterSpacing: '-0.01em' },
    h3: { fontSize: '1.125rem', fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  components: {
    MuiButton: { defaultProps: { disableElevation: true } },
    MuiAppBar: { defaultProps: { elevation: 0, color: 'transparent' } },
    MuiCard: {
      defaultProps: { elevation: 0 },
      styleOverrides: { root: { border: '1px solid', borderColor: 'divider' } },
    },
    MuiCssBaseline: {
      styleOverrides: {
        /**
         * Financial figures are read in columns, so every numeral must occupy
         * the same width. Without this, price and P&L tables visibly wobble.
         */
        '.tabular': { fontVariantNumeric: 'tabular-nums' },
        body: { fontVariantNumeric: 'tabular-nums' },
      },
    },
  },
});
