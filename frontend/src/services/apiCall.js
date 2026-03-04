// frontend/src/services/apiCall.js
import axios from 'axios';
import { logger } from '@/services/logger';

/**
 * Wraps an async store action with standardized error handling.
 *
 * @param {string} category - Logger category ('radio', 'store', etc.)
 * @param {string} message - Error message logged on failure
 * @param {() => Promise<any>} fn - Async callback containing the action body
 * @param {{ rethrow?: boolean, fallback?: any }} [options]
 *   - rethrow: if true, logs and re-throws on error (default: false)
 *   - fallback: value returned on swallowed error (default: false)
 * @returns {Promise<any>}
 */
export async function apiCall(category, message, fn, { rethrow = false, fallback = false } = {}) {
  try {
    return await fn();
  } catch (error) {
    // Silently swallow cancelled requests (AbortController abort)
    if (axios.isCancel(error) || error.name === 'AbortError') return fallback;
    logger.error(category, message, error);
    if (rethrow) throw error;
    return fallback;
  }
}
