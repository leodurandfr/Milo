// frontend/src/services/errorReporter.js
import { apiCall } from './apiCall';

// Rate-limited error reporter to backend (max 1 per second).
// Uses apiCall.post with logLevel: 'debug' so a failed report never floods the
// console (it would be a debug message, not an error — preventing recursion
// even though logger.{error,debug} only write to console).
let lastErrorTime = 0;

/** True when the report was sent, false when the rate limit dropped it. */
export function reportError(source, error, info = '') {
  const now = Date.now();
  if (now - lastErrorTime < 1000) return false;
  lastErrorTime = now;

  apiCall.post('/api/errors', {
    source,
    error: typeof error === 'string' ? error : (error?.message || String(error)),
    info: info || (error?.stack || ''),
  }, {
    category: 'reporter',
    message: 'Error reporter POST failed',
    logLevel: 'debug',
  });
  return true;
}
