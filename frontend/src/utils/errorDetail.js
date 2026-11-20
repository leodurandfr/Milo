// frontend/src/utils/errorDetail.js

/**
 * The server's own words for a failed request, as text a screen can show.
 *
 * FastAPI answers a refused body (422) with a list of validation errors, one
 * object per field, rather than a sentence: drawn as is, it reads
 * `[object Object]`. Each entry keeps its message, without the `Value error, `
 * Pydantic puts before a validator's own sentence. A string passes unchanged,
 * and so does an object: a route that answers a structured detail (a code a
 * screen translates, as the adoption errors are) is read by its caller.
 *
 * @param {unknown} detail - `response.data.detail`
 * @returns {unknown} a string for a list, the detail itself otherwise
 */
export function describeDetail(detail) {
  if (!Array.isArray(detail)) return detail;
  return detail
    .map(entry => (typeof entry?.msg === 'string' ? entry.msg.replace(/^Value error, /, '') : String(entry)))
    .join('\n');
}
