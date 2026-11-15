import Button from '@mui/material/Button';
import Link from '@mui/material/Link';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link as RouterLink, Navigate, useLocation, useNavigate } from 'react-router';
import { z } from 'zod';
import { useLogin } from '../api/useAuth';
import { AuthFormLayout } from '../components/AuthFormLayout';
import { useAuthStore } from '../store/authStore';

const schema = z.object({
  email: z.email('Enter a valid email address.'),
  // No length rule here on purpose: restating the policy on the sign-in form
  // only tells an attacker what to generate. The server enforces it.
  password: z.string().min(1, 'Enter your password.'),
});

type LoginForm = z.infer<typeof schema>;

interface LocationState {
  from?: { pathname: string };
}

export function LoginPage() {
  const status = useAuthStore((state) => state.status);
  const navigate = useNavigate();
  const location = useLocation();
  const login = useLogin();

  const { register, handleSubmit, formState } = useForm<LoginForm>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  });

  const destination = (location.state as LocationState | null)?.from?.pathname ?? '/';

  // mutate + onSuccess rather than await mutateAsync: a failed sign-in is an
  // expected outcome shown via `login.error`, not an unhandled rejection.
  const onSubmit = handleSubmit((values) => {
    login.mutate(values, {
      onSuccess: () => void navigate(destination, { replace: true }),
    });
  });

  if (status === 'authenticated') return <Navigate to={destination} replace />;

  return (
    <AuthFormLayout
      title="Sign in"
      subtitle="Welcome back to Stonks Empire."
      error={login.error}
      onSubmit={(event) => void onSubmit(event)}
      footer={
        <Typography variant="body2" color="text.secondary">
          No account?{' '}
          <Link component={RouterLink} to="/register">
            Create one
          </Link>
        </Typography>
      }
    >
      <TextField
        label="Email"
        type="email"
        autoComplete="email"
        autoFocus
        error={Boolean(formState.errors.email)}
        helperText={formState.errors.email?.message}
        {...register('email')}
      />
      <TextField
        label="Password"
        type="password"
        autoComplete="current-password"
        error={Boolean(formState.errors.password)}
        helperText={formState.errors.password?.message}
        {...register('password')}
      />
      <Button type="submit" variant="contained" size="large" loading={login.isPending}>
        Sign in
      </Button>
    </AuthFormLayout>
  );
}
