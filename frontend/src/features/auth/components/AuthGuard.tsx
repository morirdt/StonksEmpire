import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import { Navigate, Outlet, useLocation } from 'react-router';
import { useAuthStore } from '../store/authStore';

/**
 * Layout route wrapping everything that needs a signed-in user.
 *
 * While the boot-time refresh is still in flight the status is `unknown`, and
 * we must render neither the page nor a redirect — redirecting here is what
 * bounces a logged-in user to /login on every reload.
 */
export function AuthGuard() {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  if (status === 'unknown') {
    return (
      <Box sx={{ display: 'grid', placeItems: 'center', minHeight: '60dvh' }}>
        <CircularProgress aria-label="Restoring your session" />
      </Box>
    );
  }

  if (status === 'anonymous') {
    // `state.from` preserves where they were headed, so signing in returns
    // them there instead of dumping everyone on the dashboard.
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
