/**
 * The page learns it runs inside Milo-iOS from the user agent the app's web
 * view sets (utils/iosApp.js), and that string lives in the app, not here. Read
 * from the vendored ViewController.swift, so a refresh of the snapshot that
 * changes it fails here — instead of the dock's volume silently coming back
 * inside the app, which never shows it.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { isMiloIosApp } from '@/utils/iosApp';

const HERE = dirname(fileURLToPath(import.meta.url));
const VIEW_CONTROLLER = readFileSync(
  resolve(HERE, '../../../backend/tests/contracts/vendor/milo-ios/ViewController.swift'), 'utf8');

function appUserAgent() {
  const m = VIEW_CONTROLLER.match(/customUserAgent\s*=\s*"([^"]+)"/);
  if (!m) throw new Error('customUserAgent not found in the vendored ViewController.swift');
  return m[1];
}

describe('Milo-iOS user agent', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('is recognized as the app', () => {
    vi.stubGlobal('navigator', { userAgent: appUserAgent() });
    expect(isMiloIosApp()).toBe(true);
  });

  it('is not mistaken for mobile Safari', () => {
    vi.stubGlobal('navigator', {
      userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
    });
    expect(isMiloIosApp()).toBe(false);
  });
});
