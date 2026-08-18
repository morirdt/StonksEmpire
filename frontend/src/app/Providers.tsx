import type { ReactNode } from 'react';
import CssBaseline from '@mui/material/CssBaseline';
import { ThemeProvider } from '@mui/material/styles';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { queryClient as defaultQueryClient } from '@/lib/queryClient';
import { theme } from '@/theme';

interface ProvidersProps {
  children: ReactNode;
  /** Tests inject a throwaway client so caches never leak between cases. */
  client?: QueryClient;
}

/**
 * Single place where app-wide context is composed, so tests can render the
 * same provider stack the app uses.
 */
export function Providers({ children, client = defaultQueryClient }: ProvidersProps) {
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme} defaultMode="dark">
        <CssBaseline enableColorScheme />
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
