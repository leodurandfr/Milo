// frontend/tests/helpers/stripComments.js
/**
 * Remove comments from source read as text, so a rule reads code and never the
 * prose above it.
 *
 * Shared because both consumers learned it the hard way. `artworkParity` asserts
 * non-presence — `not.toMatch(/album_art_url/)` — and writing
 * `// The helper owns album_art_url; do not read it here.` at exactly the right
 * place in useScreensaver.js turned it red. Documenting a rule where it applies
 * is the most natural thing a reader can do; it must not read as a violation.
 *
 * Handles block comments, line comments and HTML comments (a .vue template's
 * prose lives in `<!-- -->`). Line comments are matched greedily to end of line,
 * so do not hand this a file with `://` in it — none of the callers' inputs
 * have any.
 */
export function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\/.*$/gm, '');
}
