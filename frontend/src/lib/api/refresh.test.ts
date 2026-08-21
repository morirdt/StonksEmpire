/**
 * The 401 → refresh → retry path.
 *
 * The property that matters most is that concurrent 401s share ONE refresh.
 * Refresh tokens rotate and the backend treats a replayed token as theft, so a
 * client that fires one refresh per failed request would spend four spent
 * tokens and get its own session family revoked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from './client';
import { session } from './session';

const REFRESH_URL = 'http://localhost:8000/api/v1/auth/refresh';

function problem(status: number, code: string) {
  return new Response(
    JSON.stringify({
      type: `https://stonks.empire/errors/${code}`,
      title: code,
      status,
      detail: 'nope',
      instance: '/api/v1/auth/me',
      code,
      correlation_id: 'corr-1',
    }),
    { status, headers: { 'content-type': 'application/problem+json' } },
  );
}

function ok(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const user = {
  id: '018f-user',
  email: 'a@example.com',
  display_name: null,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

/** Typing the mock keeps its implementations returning Promise<Response>
 * rather than the `void` an untyped vi.fn() infers. */
type FetchLike = (input: Request | string) => Promise<Response>;

let fetchMock: ReturnType<typeof vi.fn<FetchLike>>;

beforeEach(() => {
  session.setAccessToken('expired-token');
  session.onUnauthenticated(null);
  fetchMock = vi.fn<FetchLike>();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  session.clear();
  session.onUnauthenticated(null);
});

describe('refresh middleware', () => {
  it('refreshes once and replays the original request', async () => {
    fetchMock.mockImplementation((input: Request | string) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url === REFRESH_URL) return Promise.resolve(ok({ access_token: 'fresh' }));
      const request = input as Request;
      if (request.headers.get('Authorization') === 'Bearer fresh') {
        return Promise.resolve(ok(user));
      }
      return Promise.resolve(problem(401, 'authentication_failed'));
    });

    const { data } = await api.GET('/api/v1/auth/me');

    expect(data).toEqual(user);
    expect(session.getAccessToken()).toBe('fresh');
    const refreshCalls = fetchMock.mock.calls.filter(([input]) => {
      const url = typeof input === 'string' ? input : input.url;
      return url === REFRESH_URL;
    });
    expect(refreshCalls).toHaveLength(1);
  });

  it('shares a single refresh across concurrent 401s', async () => {
    let refreshCount = 0;
    fetchMock.mockImplementation((input: Request | string) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url === REFRESH_URL) {
        refreshCount += 1;
        // Resolve on a later tick so all four 401s overlap in flight.
        return new Promise((resolve) =>
          setTimeout(() => resolve(ok({ access_token: 'fresh' })), 10),
        );
      }
      const request = input as Request;
      if (request.headers.get('Authorization') === 'Bearer fresh') {
        return Promise.resolve(ok(user));
      }
      return Promise.resolve(problem(401, 'authentication_failed'));
    });

    const results = await Promise.all([
      api.GET('/api/v1/auth/me'),
      api.GET('/api/v1/auth/me'),
      api.GET('/api/v1/auth/me'),
      api.GET('/api/v1/auth/me'),
    ]);

    expect(refreshCount).toBe(1);
    expect(results.every((result) => result.data !== undefined)).toBe(true);
  });

  it('clears the session and notifies when the refresh fails', async () => {
    const onUnauthenticated = vi.fn();
    session.onUnauthenticated(onUnauthenticated);

    fetchMock.mockImplementation((input: Request | string) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url === REFRESH_URL) return Promise.resolve(problem(401, 'authentication_failed'));
      return Promise.resolve(problem(401, 'authentication_failed'));
    });

    await expect(api.GET('/api/v1/auth/me')).rejects.toBeInstanceOf(ApiError);

    expect(session.getAccessToken()).toBeNull();
    expect(onUnauthenticated).toHaveBeenCalledOnce();
  });

  it('does not try to refresh a failed login', async () => {
    fetchMock.mockResolvedValue(problem(401, 'authentication_failed'));

    await expect(
      api.POST('/api/v1/auth/login', { body: { email: 'a@b.com', password: 'x' } }),
    ).rejects.toBeInstanceOf(ApiError);

    const refreshCalls = fetchMock.mock.calls.filter(([input]) => {
      const url = typeof input === 'string' ? input : input.url;
      return url === REFRESH_URL;
    });
    expect(refreshCalls).toHaveLength(0);
  });

  it('attaches the bearer token when one is held', async () => {
    fetchMock.mockImplementation((input: Request | string) => {
      const request = input as Request;
      expect(request.headers.get('Authorization')).toBe('Bearer expired-token');
      return Promise.resolve(ok(user));
    });

    await api.GET('/api/v1/auth/me');

    expect(fetchMock).toHaveBeenCalled();
  });

  it('sends no Authorization header when signed out', async () => {
    session.clear();
    fetchMock.mockImplementation((input: Request | string) => {
      const request = input as Request;
      expect(request.headers.get('Authorization')).toBeNull();
      return Promise.resolve(ok({ name: 'Stonks Empire' }));
    });

    await api.GET('/api/v1/meta');

    expect(fetchMock).toHaveBeenCalled();
  });
});
