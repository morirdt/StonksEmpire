import Button from '@mui/material/Button';
import Link from '@mui/material/Link';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { Link as RouterLink, Navigate, useNavigate } from 'react-router';
import { z } from 'zod';
import { useRegister } from '../api/useAuth';
import { AuthFormLayout } from '../components/AuthFormLayout';
import { useAuthStore } from '../store/authStore';

/** Mirrors the server's policy, so the user hears about it before submitting. */
const PASSWORD_MIN_LENGTH = 12;
const PASSWORD_MAX_LENGTH = 128;

const schema = z.object({
  email: z.email('Enter a valid email address.'),
  password: z
    .string()
    .min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`)
    .max(PASSWORD_MAX_LENGTH, `Use at most ${PASSWORD_MAX_LENGTH} characters.`),
  display_name: z.string().max(80, 'Use at most 80 characters.').optional(),
});

type RegisterForm = z.infer<typeof schema>;

export function RegisterPage() {
  const status = useAuthStore((state) => state.status);
  const navigate = useNavigate();
  const registerUser = useRegister();

  const { register, handleSubmit, formState } = useForm<RegisterForm>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '', display_name: '' },
  });

  // mutate + onSuccess rather than await mutateAsync: a duplicate email is an
  // expected outcome shown via `registerUser.error`, not an unhandled rejection.
  const onSubmit = handleSubmit((values) => {
    registerUser.mutate(
      {
        email: values.email,
        password: values.password,
        display_name: values.display_name?.trim() || null,
      },
      { onSuccess: () => void navigate('/', { replace: true }) },
    );
  });

  if (status === 'authenticated') return <Navigate to="/" replace />;

  return (
    <AuthFormLayout
      title="Create an account"
      subtitle="Watchlists, screeners, alerts, and a trading journal."
      error={registerUser.error}
      onSubmit={(event) => void onSubmit(event)}
      footer={
        <Typography variant="body2" color="text.secondary">
          Already registered?{' '}
          <Link component={RouterLink} to="/login">
            Sign in
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
        label="Display name"
        autoComplete="nickname"
        error={Boolean(formState.errors.display_name)}
        helperText={formState.errors.display_name?.message ?? 'Optional.'}
        {...register('display_name')}
      />
      <TextField
        label="Password"
        type="password"
        autoComplete="new-password"
        error={Boolean(formState.errors.password)}
        helperText={
          formState.errors.password?.message ?? `At least ${PASSWORD_MIN_LENGTH} characters.`
        }
        {...register('password')}
      />
      <Button type="submit" variant="contained" size="large" loading={registerUser.isPending}>
        Create account
      </Button>
    </AuthFormLayout>
  );
}
