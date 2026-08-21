import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Container from '@mui/material/Container';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import Toolbar from '@mui/material/Toolbar';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { useColorScheme } from '@mui/material/styles';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import LightModeIcon from '@mui/icons-material/LightMode';
import { Outlet } from 'react-router';
import { useAuthBootstrap } from '@/features/auth/api/useAuthBootstrap';
import { useLogout } from '@/features/auth/api/useAuth';
import { useAuthStore } from '@/features/auth/store/authStore';

function ColorSchemeToggle() {
  const { mode, setMode } = useColorScheme();
  // Undefined until the scheme is resolved on the client; render nothing
  // rather than flashing the wrong icon.
  if (!mode) return null;

  const next = mode === 'dark' ? 'light' : 'dark';
  return (
    <Tooltip title={`Switch to ${next} mode`}>
      <IconButton onClick={() => setMode(next)} size="small" aria-label={`Switch to ${next} mode`}>
        {mode === 'dark' ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}
      </IconButton>
    </Tooltip>
  );
}

function AccountControls() {
  const user = useAuthStore((state) => state.user);
  const status = useAuthStore((state) => state.status);
  const logout = useLogout();

  if (status !== 'authenticated' || !user) return null;

  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
      <Typography variant="body2" color="text.secondary">
        {user.display_name ?? user.email}
      </Typography>
      <Button size="small" onClick={() => logout.mutate()} loading={logout.isPending}>
        Sign out
      </Button>
    </Stack>
  );
}

export function AppLayout() {
  // One silent refresh per app load, so a reload keeps the user signed in.
  useAuthBootstrap();

  return (
    <Box sx={{ minHeight: '100dvh', bgcolor: 'background.default' }}>
      <AppBar position="sticky" sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
        <Toolbar>
          <Typography variant="h3" component="span" sx={{ flexGrow: 1 }}>
            Stonks Empire
          </Typography>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
            <AccountControls />
            <ColorSchemeToggle />
          </Stack>
        </Toolbar>
      </AppBar>
      <Container maxWidth="lg" sx={{ py: 4 }}>
        <Outlet />
      </Container>
    </Box>
  );
}
