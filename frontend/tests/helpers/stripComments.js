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
 * prose lives in `<!-- -->`). It is a scanner, not a set of regexes, because a
 * comment marker inside a literal is not one: the `/*` of a glob pattern
 * (`import.meta.glob('@/assets/app-icons/*.svg')`) paired with the next `*\/`
 * and hid two thirds of AppIcon.vue from every rule importing this. So strings,
 * template literals (their `${}` scanned as code) and regex literals are copied
 * through untouched. A quote or a regex that does not close on its own line is
 * not taken for one — template prose holds apostrophes — so a wrong guess costs
 * that line at most. A `//` right after a colon is a URL's `://` in prose.
 */
export function stripComments(source) {
  const out = [];
  scanCode(source, 0, out, false);
  return out.join('');
}

const REGEX_AFTER_CHAR = new Set('(,=:[!&|?{};+-*%~^'.split(''));
const REGEX_AFTER_WORD = /(?:^|[^\w$])(?:return|typeof|case|do|else|in|of|new|delete|void|throw|instanceof|yield|await)$/;

/** Copy code from `i`; when `nested` (inside `${`), stop at its closing brace. */
function scanCode(src, i, out, nested) {
  let depth = 0;
  while (i < src.length) {
    const c = src[i];
    const next = src[i + 1];
    if (nested && c === '{') depth++;
    if (nested && c === '}') {
      if (depth === 0) return i;
      depth--;
    }
    if (c === '/' && next === '*') {
      i = skipPast(src, '*/', i + 2);
    } else if (c === '/' && next === '/' && src[i - 1] !== ':') {
      const end = src.indexOf('\n', i);
      i = end < 0 ? src.length : end;
    } else if (src.startsWith('<!--', i)) {
      i = skipPast(src, '-->', i + 4);
    } else if (c === "'" || c === '"') {
      i = copyLiteral(src, i, out, quotedEnd(src, i, c));
    } else if (c === '`') {
      i = copyTemplate(src, i, out);
    } else if (c === '/' && regexAllowed(out)) {
      i = copyLiteral(src, i, out, regexEnd(src, i));
    } else {
      out.push(c);
      i++;
    }
  }
  return i;
}

function skipPast(src, marker, from) {
  const end = src.indexOf(marker, from);
  return end < 0 ? src.length : end + marker.length;
}

/** Copy the literal `[i, end)`, or only its opening character when it never closed. */
function copyLiteral(src, i, out, end) {
  if (end < 0) {
    out.push(src[i]);
    return i + 1;
  }
  out.push(src.slice(i, end));
  return end;
}

function quotedEnd(src, i, quote) {
  for (let j = i + 1; j < src.length; j++) {
    if (src[j] === '\\') j++;
    else if (src[j] === quote) return j + 1;
    else if (src[j] === '\n') return -1;
  }
  return -1;
}

function regexEnd(src, i) {
  let inClass = false;
  for (let j = i + 1; j < src.length; j++) {
    const c = src[j];
    if (c === '\\') j++;
    else if (c === '\n') return -1;
    else if (c === '[') inClass = true;
    else if (c === ']') inClass = false;
    else if (c === '/' && !inClass) {
      let k = j + 1;
      while (k < src.length && /[a-z]/i.test(src[k])) k++;
      return k;
    }
  }
  return -1;
}

function copyTemplate(src, i, out) {
  out.push('`');
  let j = i + 1;
  while (j < src.length) {
    const c = src[j];
    if (c === '\\') {
      out.push(src.slice(j, j + 2));
      j += 2;
    } else if (c === '`') {
      out.push('`');
      return j + 1;
    } else if (c === '$' && src[j + 1] === '{') {
      out.push('${');
      j = scanCode(src, j + 2, out, true);
      if (j < src.length) {
        out.push('}');
        j++;
      }
    } else {
      out.push(c);
      j++;
    }
  }
  return j;
}

/** A `/` opens a regex where an expression may start, judged from the code before it. */
function regexAllowed(out) {
  let code = '';
  for (let k = out.length - 1; k >= 0 && code.length < 12; k--) code = (out[k] + code).trimEnd();
  if (!code) return true;
  const last = code[code.length - 1];
  if (REGEX_AFTER_CHAR.has(last)) return true;
  if (last === '>') return code[code.length - 2] === '=';
  return REGEX_AFTER_WORD.test(code);
}
