import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { Link } from 'react-router';

export function NotFoundPage() {
  return (
    <Stack spacing={2} sx={{ alignItems: 'flex-start' }}>
      <Typography variant="h1">404</Typography>
      <Typography color="text.secondary">That page does not exist.</Typography>
      <Button component={Link} to="/" variant="contained">
        Back to dashboard
      </Button>
    </Stack>
  );
}
