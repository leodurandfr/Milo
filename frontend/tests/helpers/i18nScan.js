// frontend/tests/helpers/i18nScan.js
/**
 * Scans `src/` for i18n key references.
 *
 * Two sets are returned, deliberately asymmetric because a false positive on
 * either side would break CI for the wrong reason:
 *
 *  - `calledKeys` — high confidence only: literal keys passed straight to
 *    `t()` / `$t()` / `i18n.t()`. Used to assert "this key must exist in
 *    english.json". A looser rule here would flag ordinary strings as missing
 *    translations.
 *  - `referencedKeys` / `dynamicPrefixes` — permissive: also key-shaped string
 *    literals sitting in lookup maps (SetupWizard's `stepTitles`, AudioStep's
 *    `categoryLabelMap`, multiroomStore's `MULTIROOM_ERROR_KEYS`) and template
 *    prefixes (`radio.genres.${key}`). Used to assert "this key is used
 *    somewhere". A stricter rule here would report live keys as dead.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
export const SRC_DIR = resolve(HERE, '../../src');
export const LOCALES_DIR = join(SRC_DIR, 'locales');

// `t('x')`, `$t('x')` and `i18n.t('x')` — but not `store.t(` or `apiCall.get(`.
const CALL_RE = /(?:(?<![\w$.])\$?t|\bi18n\.t)\(\s*['"]([^'"]+)['"]/g;
// The same three call forms with a template literal: `t(`prefix.${id}`)`.
const CALL_TEMPLATE_RE = /(?:(?<![\w$.])\$?t|\bi18n\.t)\(\s*`([^`]*?)\$\{/g;
// A prefix built into a variable first: const path = `radio.genres.${key}`.
const ASSIGNED_TEMPLATE_RE = /(?:const|let)\s+\w+\s*=\s*`([a-z][\w]*(?:\.[\w]+)*\.)\$\{/g;
// A key-shaped literal anywhere in a file that also talks to i18n.
const KEY_LITERAL_RE = /['"]([a-z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)['"]/g;
const USES_I18N_RE = /(?<![\w$.])\$?t\(|\bi18n\.t\(|useI18n\(/;

const NON_KEY_SUFFIX = /\.(vue|js|json|css|svg|png|jpg|webp|html|py|sh|md|local)$/;

function isKeyShaped(value) {
  return (
    /^[a-z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$/.test(value) &&
    !NON_KEY_SUFFIX.test(value) &&
    !value.includes('/')
  );
}

function collectSourceFiles(dir, files = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const fullPath = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || fullPath === LOCALES_DIR) continue;
      collectSourceFiles(fullPath, files);
    } else if (entry.name.endsWith('.vue') || entry.name.endsWith('.js')) {
      files.push(fullPath);
    }
  }
  return files;
}

export function scanI18nUsage() {
  const calledKeys = new Map();      // key → first file that calls it
  const referencedKeys = new Set();
  const dynamicPrefixes = new Set();
  const files = collectSourceFiles(SRC_DIR);

  for (const file of files) {
    const content = readFileSync(file, 'utf8');
    const relative = file.slice(SRC_DIR.length + 1);

    for (const [, key] of content.matchAll(CALL_RE)) {
      if (!isKeyShaped(key)) continue; // e.g. t('') guards, or a plain word
      if (!calledKeys.has(key)) calledKeys.set(key, relative);
      referencedKeys.add(key);
    }

    if (!USES_I18N_RE.test(content)) continue;

    for (const re of [CALL_TEMPLATE_RE, ASSIGNED_TEMPLATE_RE]) {
      for (const [, prefix] of content.matchAll(re)) {
        if (prefix.startsWith('/') || prefix.startsWith('http')) continue;
        if (prefix.includes('.')) dynamicPrefixes.add(prefix);
      }
    }

    for (const [, key] of content.matchAll(KEY_LITERAL_RE)) {
      if (isKeyShaped(key)) referencedKeys.add(key);
    }
  }

  return { calledKeys, referencedKeys, dynamicPrefixes, fileCount: files.length };
}

/** Flatten a locale object to dot-notation leaves. */
export function flattenKeys(obj, prefix = '') {
  const flat = {};
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      Object.assign(flat, flattenKeys(value, path));
    } else {
      flat[path] = value;
    }
  }
  return flat;
}

export function loadLocales() {
  const locales = {};
  for (const file of readdirSync(LOCALES_DIR).filter(f => f.endsWith('.json'))) {
    const name = file.replace(/\.json$/, '');
    locales[name] = flattenKeys(JSON.parse(readFileSync(join(LOCALES_DIR, file), 'utf8')));
  }
  return locales;
}
