/**
 * Typed API client.
 *
 * `paths` is generated from the backend's OpenAPI schema (`make gen-api`), so
 * every URL, parameter, and response body below is checked against the real
 * contract at compile time. Never hand-edit schema.ts.
 */
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './schema';
import { session } from './session';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

const REFRESH_URL = `${API_BASE_URL}/api/v1/auth/refresh`;

/**
 * A 401 from these is a real credential failure, not an expired access token,
 * so retrying them would loop.
 */
const NEVER_RETRY = new Set([
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/refresh',
  '/api/v1/auth/logout',
]);

/** RFC 9457 problem+json — the single error shape the API returns. */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  code: string;
  correlation_id: string | null;
  errors?: { location: (string | number)[]; message: string; type: string }[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly correlationId: string | null;
  readonly problem: ProblemDetail | null;

  constructor(status: number, problem: ProblemDetail | null, fallback?: string) {
    super(problem?.detail ?? fallback ?? `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = problem?.code ?? 'unknown_error';
    this.correlationId = problem?.correlation_id ?? null;
    this.problem = problem;
  }
}

/** Error bodies are not guaranteed to be problem+json (proxies, gateways). */
async function readProblemDetail(response: Response): Promise<ProblemDetail | null> {
  try {
    return (await response.clone().json()) as ProblemDetail;
  } catch {
    return null;
  }
}

/**
 * The single in-flight refresh.
 *
 * When a page fires five queries at once and the access token has expired, all
 * five come back 401 together. Without this they would each start their own
 * refresh, and because refresh tokens rotate, four of those five would present
 * an already-spent token — which the backend correctly reads as theft and
 * punishes by killing the whole session family. One shared promise is not an
 * optimisation here; it is what stops the app logging itself out.
 */
let inFlightRefresh: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  inFlightRefresh ??= (async () => {
    try {
      const response = await fetch(REFRESH_URL, {
        method: 'POST',
        // The refresh token rides as an HttpOnly cookie, so it has to be sent.
        credentials: 'include',
      });
      if (!response.ok) return null;

      const body = (await response.json()) as { access_token: string };
      session.setAccessToken(body.access_token);
      return body.access_token;
    } catch {
      return null;
    } finally {
      // Cleared before the promise settles, so callers already awaiting this
      // one still share it while the next 401 starts a fresh attempt.
      inFlightRefresh = null;
    }
  })();

  return inFlightRefresh;
}

/**
 * Attaches the bearer token, retries once after a silent refresh, and
 * normalises every non-2xx into an ApiError so callers never branch on
 * `data` vs `error` shapes and React Query sees a real throw.
 */
const authMiddleware: Middleware = {
  onRequest({ request }) {
    const token = session.getAccessToken();
    if (token) request.headers.set('Authorization', `Bearer ${token}`);
    return request;
  },

  async onResponse({ request, response, schemaPath }) {
    let finalResponse = response;

    if (response.status === 401 && !NEVER_RETRY.has(schemaPath)) {
      const token = await refreshAccessToken();

      if (token === null) {
        session.clear();
        session.notifyUnauthenticated();
      } else {
        const headers = new Headers(request.headers);
        headers.set('Authorization', `Bearer ${token}`);
        finalResponse = await fetch(new Request(request, { headers }));
      }
    }

    if (finalResponse.ok) return finalResponse;

    const problem = await readProblemDetail(finalResponse);
    throw new ApiError(finalResponse.status, problem, finalResponse.statusText);
  },
};

export const api = createClient<paths>({
  baseUrl: API_BASE_URL,
  // The refresh cookie must ride along on the auth endpoints.
  credentials: 'include',
  // Resolved per call instead of captured at module load, so a stubbed or
  // polyfilled global fetch is actually used. Capturing it here would bind the
  // client to whatever existed when this module was first imported.
  fetch: (request) => globalThis.fetch(request),
});

api.use(authMiddleware);
