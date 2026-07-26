// frontend/tests/helpers/apiCallMock.js
/**
 * Mock module for `@/services/apiCall` — the single HTTP boundary of the app.
 *
 * Store tests target this boundary, never axios: `apiCall.js` is the only file
 * allowed to import axios, so a store test that mocks axios is asserting through
 * a layer it doesn't own (that assumption is what broke the suite in the first
 * place).
 *
 * Usage in a test file:
 *   vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
 *   import { apiCall } from '@/services/apiCall';
 *   import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
 *
 *   beforeEach(() => resetApiCallMock());
 *   apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));
 */
import { vi } from 'vitest';

const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete'];

/** Shape returned by apiCall.{get,post,…} on success. */
export function ok(data = {}) {
  return { ok: true, data, error: null };
}

/** Shape returned on a failed request (the real helper swallows and reports). */
export function fail(detail = 'Network error', status = 500) {
  return { ok: false, data: null, error: { detail, status } };
}

/**
 * Callback form: `apiCall(category, message, fn, options)`. The real one runs
 * fn(), swallows throws (returning `fallback`) and rethrows when asked — the
 * mock reproduces that so stores under test take their real control flow.
 */
export const apiCall = vi.fn();

for (const method of HTTP_METHODS) {
  apiCall[method] = vi.fn();
}

/**
 * Restore default behaviour and drop every recorded call / queued `…Once`.
 * Call from beforeEach: vi.clearAllMocks() keeps once-queues alive, which
 * would leak a stubbed response into the next test.
 */
export function resetApiCallMock() {
  apiCall.mockReset();
  apiCall.mockImplementation(
    async (category, message, fn, { fallback = false, rethrow = false } = {}) => {
      try {
        return await fn();
      } catch (error) {
        if (rethrow) throw error;
        return fallback;
      }
    },
  );
  for (const method of HTTP_METHODS) {
    apiCall[method].mockReset();
    apiCall[method].mockResolvedValue(ok());
  }
}

resetApiCallMock();
