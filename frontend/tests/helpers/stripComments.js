// frontend/tests/helpers/stripComments.js
/**
 * Remove comments from source read as text, so a rule reads code and never the
 * prose above it.
 *
 * Shared because both consumers learned it the hard way. `artworkParity` asserts
 * non-presence — `not.toMatch(/album_art_url/)` — and writing
 * `// The helper owns album_art_url; do not read it here.` at exactly the right
 * place in a view it scans turned it red. Documenting a rule where it applies
 * is the most natural thing a reader can do; it must not read as a violation.
 *
 * Handles block comments, line comments and HTML comments (a .vue template's
 * prose lives in `<!-- -->`). A `//` right after a colon is a URL's `://`, not a
 * comment: cutting there hid the rest of every line holding a URL from each rule
 * that reads src/ through this helper.
 */
export function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/(?<!:)\/\/.*$/gm, '');
}
