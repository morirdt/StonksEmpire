import { describe, expect, it } from 'vitest';
import { ApiError, type ProblemDetail } from './client';

const problem: ProblemDetail = {
  type: 'https://stonks.empire/errors/not_found',
  title: 'Resource Not Found',
  status: 404,
  detail: 'Watchlist 42 does not exist.',
  instance: '/api/v1/watchlists/42',
  code: 'not_found',
  correlation_id: 'corr-7',
};

describe('ApiError', () => {
  it('takes its message from the problem detail', () => {
    const error = new ApiError(404, problem);

    expect(error.message).toBe('Watchlist 42 does not exist.');
    expect(error.code).toBe('not_found');
    expect(error.correlationId).toBe('corr-7');
    expect(error).toBeInstanceOf(Error);
  });

  it('degrades gracefully when the body is not problem+json', () => {
    const error = new ApiError(502, null, 'Bad Gateway');

    expect(error.message).toBe('Bad Gateway');
    expect(error.code).toBe('unknown_error');
    expect(error.correlationId).toBeNull();
  });
});
