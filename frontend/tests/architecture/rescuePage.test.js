// frontend/tests/architecture/rescuePage.test.js
/**
 * The rescue page is spelled in three places that cannot import each other:
 * the nginx site that serves it as the body of the statuses it answers itself
 * (rootfs/etc/nginx/sites-enabled/milo), the Vite build that puts it in dist/
 * (vite.config.js), and the app that leaves for it (utils/backendDown.js).
 *
 * What breaks when this fails: a renamed page or entry is a 404 nginx answers
 * for the page itself, so a unit whose backend is gone shows nginx's bare
 * "502 Bad Gateway", or the app sits on "Connection lost" forever because the
 * status it waits for is one nginx never gives. Nothing else notices: the
 * backend is down, so no request reports it. Read from source, nothing mocked.
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { RESCUE_PAGE, isBackendDown } from '@/utils/backendDown';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '../..');
const NGINX_SITE = resolve(FRONTEND, '../rootfs/etc/nginx/sites-enabled/milo');

/** `error_page <codes…> <uri>;` directives of the site: [{ codes, uri }]. */
function errorPages(site) {
  return [...site.matchAll(/^\s*error_page\s+([^;]+);/gm)].map(([, args]) => {
    const parts = args.trim().split(/\s+/);
    return { codes: parts.slice(0, -1).map(Number), uri: parts.at(-1) };
  });
}

/** The HTML files `build.rollupOptions.input` names, relative to frontend/. */
function viteEntries(config) {
  return [...config.matchAll(/path\.resolve\(__dirname,\s*'([^']+\.html)'\)/g)].map(([, file]) => file);
}

describe('the rescue page nginx serves is the one the build makes and the app opens', () => {
  const site = readFileSync(NGINX_SITE, 'utf8');
  const pages = errorPages(site);
  const entries = viteEntries(readFileSync(join(FRONTEND, 'vite.config.js'), 'utf8'));

  it('extracts a plausible surface from every side', () => {
    expect(site).toContain('proxy_pass http://milo_backend');
    expect(entries).toContain('index.html');
    expect(pages.length).toBeGreaterThan(0);
  });

  it('nginx answers its own backend failures with the page the app opens', () => {
    const rescue = pages.filter(({ uri }) => uri === RESCUE_PAGE);
    expect(rescue).toHaveLength(1);
    // Every status nginx answers with the page is one the app reads as "gone",
    // and the app reads no other: a backend error is not the backend absent.
    const codes = rescue[0].codes;
    expect(codes.length).toBeGreaterThan(0);
    expect(codes.filter((code) => !isBackendDown(code))).toEqual([]);
    expect([500, 404, 403, 200].filter(isBackendDown)).toEqual([]);
    expect([500, 502, 503, 504].filter(isBackendDown)).toEqual(codes.toSorted());
  });

  it('the page is a Vite entry, so it lands in dist/ under that name', () => {
    const file = RESCUE_PAGE.replace(/^\//, '');
    expect(entries).toContain(file);
    expect(existsSync(join(FRONTEND, file))).toBe(true);
  });

  it('the page is served directly, never through the SPA fallback', () => {
    expect(site).toMatch(new RegExp(`location = ${RESCUE_PAGE.replace(/\./g, '\\.')} \\{`));
  });
});
