import type { FormEventHandler, ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { ApiError } from '@/lib/api/client';

interface AuthFormLayoutProps {
  title: string;
  subtitle: string;
  error: unknown;
  onSubmit: FormEventHandler<HTMLFormElement>;
  children: ReactNode;
  footer: ReactNode;
}

/** Shared chrome for the login and register forms. */
export function AuthFormLayout({
  title,
  subtitle,
  error,
  onSubmit,
  children,
  footer,
}: AuthFormLayoutProps) {
  return (
    <Box sx={{ display: 'grid', placeItems: 'center', pt: 6 }}>
      <Card sx={{ width: '100%', maxWidth: 420 }}>
        <CardContent>
          <Typography variant="h2" gutterBottom>
            {title}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            {subtitle}
          </Typography>

          <form onSubmit={onSubmit} noValidate>
            <Stack spacing={2}>
              {error != null && <AuthError error={error} />}
              {children}
            </Stack>
          </form>

          <Box sx={{ mt: 3 }}>{footer}</Box>
        </CardContent>
      </Card>
    </Box>
  );
}

function AuthError({ error }: { error: unknown }) {
  const message =
    error instanceof ApiError ? error.message : 'Something went wrong. Please try again.';
  const correlationId = error instanceof ApiError ? error.correlationId : null;

  return (
    <Alert severity="error" role="alert">
      {message}
      {correlationId && (
        <Typography variant="caption" sx={{ display: 'block', mt: 0.5, opacity: 0.8 }}>
          Reference: {correlationId}
        </Typography>
      )}
    </Alert>
  );
}
