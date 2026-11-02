// frontend/tests/pure/serverInstance.test.js
/**
 * App.vue reveals the dock when the backend restarted, and must not when the
 * socket merely reconnected to the same backend — a phone back from the
 * background reconnects too, and the dock popping up on every return is what
 * 6547247b removed. The two look identical from the socket; only the
 * `server_instance` the handshake carries tells them apart.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import { isBackendRestart, isStaleBuild } from '@/utils/serverInstance';

describe('isBackendRestart', () => {
  it('is false for the first backend the app meets (the boot reveals the dock)', () => {
    expect(isBackendRestart(null, 'a1')).toBe(false);
  });

  it('is false for a reconnect to the same backend', () => {
    expect(isBackendRestart('a1', 'a1')).toBe(false);
  });

  it('is true for a backend that came back as a new process', () => {
    expect(isBackendRestart('a1', 'b2')).toBe(true);
  });

  it('is false for a handshake that names no backend', () => {
    expect(isBackendRestart('a1', undefined)).toBe(false);
  });
});

/**
 * An in-app Milō update swaps `dist/` and restarts the unit; a tab left open
 * keeps the old bundle, whose lazy chunks (a source never opened, a locale
 * never loaded) now answer 404 and render nothing. App.vue reloads that tab
 * when the restarted backend's index names another entry script.
 */
describe('isStaleBuild', () => {
  const served = (src) =>
    `<html><head><script type="module" crossorigin src="${src}"></script></head></html>`;

  it('asks for a reload when the restarted backend serves another frontend build', () => {
    expect(isStaleBuild('/assets/main-A.js', served('/assets/main-B.js'))).toBe(true);
  });

  it('keeps the tab when the served build is the one it runs', () => {
    expect(isStaleBuild('/assets/main-A.js', served('/assets/main-A.js'))).toBe(false);
    expect(isStaleBuild('/assets/main-A.js', '<script src="/assets/main-A.js" type="module"></script>')).toBe(false);
  });

  it('keeps the tab when either side names no entry script', () => {
    expect(isStaleBuild('/assets/main-A.js', '<html><body>502 Bad Gateway</body></html>')).toBe(false);
    expect(isStaleBuild(null, served('/assets/main-B.js'))).toBe(false);
  });

  it('reads the entry of the real index.html, not its inline boot script', () => {
    const index = readFileSync(resolve(__dirname, '../../index.html'), 'utf8');
    // The inline boot script carries no type="module" and must be skipped.
    expect(isStaleBuild('/src/main.js', index)).toBe(false);
    expect(isStaleBuild('/assets/main-A.js', index)).toBe(true);
  });
});
