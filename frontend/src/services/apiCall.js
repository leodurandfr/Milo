// frontend/src/services/apiCall.js
//
// THIS IS THE ONLY FILE ALLOWED TO IMPORT axios.
// All other modules must use `apiCall` or `apiCall.{get,post,put,patch,delete}`
// from this module for HTTP requests. The ESLint rule `no-restricted-imports`
// enforces this.

import axios from 'axios';
import { logger } from '@/services/logger';

const isCancelled = (error) => axios.isCancel(error) || error?.name === 'AbortError';

function extractErrorDetail(error) {
  return error?.response?.data?.detail || error?.message || 'Unknown error';
}

/**
 * Backward-compatible callback wrapper. Returns the fn() result on success,
 * or `fallback` on a swallowed error (logged via logger.error).
 *
 * Use this form for atomic sequences that need multiple requests sharing state.
 * For single requests, prefer the typed helpers (apiCall.{get,post,...}).
 *
 * @param {string} category - Logger category (e.g. 'radio', 'multiroom')
 * @param {string} message - Error message logged on failure
 * @param {() => Promise<any>} fn - Async callback containing the action body
 * @param {{ rethrow?: boolean, fallback?: any, errorRef?: { value: any } }} [options]
 * @returns {Promise<any>}
 */
export async function apiCall(
  category,
  message,
  fn,
  { rethrow = false, fallback = false, errorRef = null } = {}
) {
  try {
    return await fn();
  } catch (error) {
    if (isCancelled(error)) return fallback;
    logger.error(category, message, error);
    if (errorRef) errorRef.value = extractErrorDetail(error);
    if (rethrow) throw error;
    return fallback;
  }
}

/**
 * Internal HTTP request handler shared by the typed helpers.
 *
 * Returns `{ ok, data, error }`:
 *   - ok: true on HTTP success (and resilience-pattern success when checkStatus is true)
 *   - data: response.data on success, or `fallback ?? null` on error
 *   - error: null on success, `{ detail, status }` on error
 *
 * Cancellation (AbortController) is swallowed silently and returns `{ ok: false, data: fallback ?? null, error: null }`.
 */
async function httpRequest(method, url, body, options = {}) {
  const {
    category = 'api',
    message,
    params,
    signal,
    errorRef = null,
    fallback,
    rethrow = false,
    checkStatus = false,
    headers,
    timeout,
    responseType,
  } = options;

  const config = {};
  if (params !== undefined) config.params = params;
  if (signal !== undefined) config.signal = signal;
  if (headers !== undefined) config.headers = headers;
  if (timeout !== undefined) config.timeout = timeout;
  if (responseType !== undefined) config.responseType = responseType;

  try {
    let response;
    if (method === 'get' || method === 'delete') {
      response = await axios[method](url, config);
    } else {
      response = await axios[method](url, body, config);
    }

    if (checkStatus && response?.data?.status === 'error') {
      const detail = response.data?.detail || response.data?.message || 'Unknown error';
      logger.error(category, message || `${method.toUpperCase()} ${url} returned status=error`, response.data);
      if (errorRef) errorRef.value = detail;
      return { ok: false, data: response.data, error: { detail, status: 200 } };
    }

    return { ok: true, data: response.data, error: null };
  } catch (error) {
    if (isCancelled(error)) {
      return { ok: false, data: fallback ?? null, error: null };
    }
    logger.error(category, message || `${method.toUpperCase()} ${url} failed`, error);
    const detail = extractErrorDetail(error);
    const status = error?.response?.status ?? null;
    if (errorRef) errorRef.value = detail;
    if (rethrow) throw error;
    return { ok: false, data: fallback ?? null, error: { detail, status } };
  }
}

apiCall.get = (url, options) => httpRequest('get', url, null, options);
apiCall.post = (url, body, options) => httpRequest('post', url, body, options);
apiCall.put = (url, body, options) => httpRequest('put', url, body, options);
apiCall.patch = (url, body, options) => httpRequest('patch', url, body, options);
apiCall.delete = (url, options) => httpRequest('delete', url, null, options);
