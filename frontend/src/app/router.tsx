import { createBrowserRouter } from 'react-router';
import { AppLayout } from '@/components/AppLayout';
import { NotFoundPage } from '@/components/NotFoundPage';
import { AuthGuard } from '@/features/auth/components/AuthGuard';
import { LoginPage } from '@/features/auth/routes/LoginPage';
import { RegisterPage } from '@/features/auth/routes/RegisterPage';
import { ScreenerPage } from '@/features/screener/routes/ScreenerPage';
import { SymbolDetailPage } from '@/features/symbols/routes/SymbolDetailPage';
import { DashboardPage } from '@/features/system/routes/DashboardPage';
import { WatchlistsPage } from '@/features/watchlists/routes/WatchlistsPage';

/**
 * Routes are declared centrally. Feature pages are imported here and nowhere
 * else, which keeps cross-feature imports out of the codebase.
 *
 * Everything below AuthGuard requires a signed-in user; /login and /register
 * sit outside it, or signing in would be impossible.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { path: 'login', element: <LoginPage /> },
      { path: 'register', element: <RegisterPage /> },
      {
        element: <AuthGuard />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'watchlists', element: <WatchlistsPage /> },
          { path: 'screener', element: <ScreenerPage /> },
          { path: 'symbols/:ticker', element: <SymbolDetailPage /> },
        ],
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
