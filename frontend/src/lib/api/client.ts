/**
 * Typed API client.
 *
 * `paths` is generated from the backend's OpenAPI schema (`make gen-api`), so
 * every URL, parameter, and response body below is checked against the real
 * contract at compile time. Never hand-edit schema.ts.
 */
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './schema';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

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
 * Normalises every non-2xx response into an ApiError so callers never have to
 * branch on `error` vs `data` shapes, and so React Query sees a real throw.
 */
const errorMiddleware: Middleware = {
  async onResponse({ response }) {
    if (response.ok) return response;

    const problem = await readProblemDetail(response);
    throw new ApiError(response.status, problem, response.statusText);
  },
};

export const api = createClient<paths>({
  baseUrl: API_BASE_URL,
  // Phase 1 adds auth; refresh cookies must ride along from the start.
  credentials: 'include',
});

api.use(errorMiddleware);
