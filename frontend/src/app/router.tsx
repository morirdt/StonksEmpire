import { createBrowserRouter } from 'react-router';
import { AppLayout } from '@/components/AppLayout';
import { NotFoundPage } from '@/components/NotFoundPage';
import { DashboardPage } from '@/features/system/routes/DashboardPage';

/**
 * Routes are declared centrally. Feature pages are imported here and nowhere
 * else, which keeps cross-feature imports out of the codebase.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
