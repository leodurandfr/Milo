// frontend/tests/composables/i18n.test.js
/**
 * i18n — which language the interface ends up in when a locale chunk loads
 * late or not at all.
 *
 * Each locale is a separate chunk fetched over the network, so a change of
 * language waits on it. What breaks if these stop holding: the setup wizard's
 * "Deutsch" then "English" leaves the interface in German while the check mark
 * and the stored setting say English; or a chunk that fails to load (a tab
 * left on the bundle an update replaced, the network gone) is declared the
 * current language — `<html lang>` says French, every string falls back to
 * English — and asking for French again retries nothing.
 *
 * The network is the outside world here: each locale import is held behind a
 * gate (or made to throw) while its real content is kept.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { bcp47For } from '@/constants/countries';

const net = vi.hoisted(() => {
  const gate = () => {
    let open;
    const promise = new Promise((resolve) => { open = resolve; });
    return { promise, open };
  };
  return { gate, german: null, frenchFails: false, frenchLoads: 0 };
});

// Registered again before every test: vi.resetModules() keeps a mocked module
// that loaded once, so a hoisted vi.mock would serve the next test a locale
// that never waits and never fails.
function mockLocales() {
  vi.doMock('@/locales/german.json', async (importOriginal) => {
    if (net.german) await net.german.promise;
    return importOriginal();
  });
  vi.doMock('@/locales/french.json', async (importOriginal) => {
    net.frenchLoads += 1;
    if (net.frenchFails) throw new Error('Failed to fetch dynamically imported module');
    return importOriginal();
  });
}

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

let i18n;
let logger;
let apiCall;

beforeEach(async () => {
  net.german = null;
  net.frenchFails = false;
  net.frenchLoads = 0;
  // A fresh singleton, and locale factories that run again.
  vi.resetModules();
  mockLocales();
  ({ i18n } = await import('@/services/i18n'));
  ({ logger } = await import('@/services/logger'));
  logger.setLevel('none');
  ({ apiCall } = await import('@/services/apiCall'));
  document.documentElement.lang = bcp47For('english');
});

describe('handleLanguageChanged', () => {
  it('the last language asked for wins when an earlier load lands late', async () => {
    net.german = net.gate();

    const german = i18n.handleLanguageChanged('german');
    await i18n.handleLanguageChanged('english');
    net.german.open();
    await german;

    expect(i18n.currentLanguage.value).toBe('english');
    expect(document.documentElement.lang).toBe(bcp47For('english'));
  });

  it('a later language is applied once its own load lands', async () => {
    net.german = net.gate();

    const german = i18n.handleLanguageChanged('german');
    const english = i18n.handleLanguageChanged('english');
    const back = i18n.handleLanguageChanged('german');
    await english;
    net.german.open();
    await Promise.all([german, back]);

    expect(i18n.currentLanguage.value).toBe('german');
    expect(document.documentElement.lang).toBe(bcp47For('german'));
  });

  it('a locale that fails to load does not become the current language', async () => {
    const errors = vi.spyOn(logger, 'error');
    net.frenchFails = true;

    await i18n.handleLanguageChanged('french');

    expect(i18n.currentLanguage.value).toBe('english');
    expect(document.documentElement.lang).toBe(bcp47For('english'));
    expect(errors).toHaveBeenCalledTimes(1);

    // Asking again tries the load again rather than taking it as done.
    await i18n.handleLanguageChanged('french');
    expect(net.frenchLoads).toBeGreaterThan(1);
    expect(errors).toHaveBeenCalledTimes(2);
    expect(i18n.currentLanguage.value).toBe('english');

    net.frenchFails = false;
    await i18n.handleLanguageChanged('french');
    expect(i18n.currentLanguage.value).toBe('french');
    expect(document.documentElement.lang).toBe(bcp47For('french'));
  });
});

describe('setLanguage', () => {
  it('asking again for a language that failed to load retries it, though the setting already names it', async () => {
    // After the failure the stored setting already says French, so the PUT
    // and its broadcast write a value the setting holds: the App.vue watch
    // stays silent, and only this call can retry the load.
    net.frenchFails = true;
    await i18n.handleLanguageChanged('french');
    expect(i18n.currentLanguage.value).toBe('english');

    net.frenchFails = false;
    apiCall.put.mockResolvedValueOnce({ ok: true, data: { status: 'success' }, error: null });
    expect(await i18n.setLanguage('french')).toBe(true);

    expect(apiCall.put).toHaveBeenCalledWith('/api/settings/language', { language: 'french' }, expect.anything());
    expect(i18n.currentLanguage.value).toBe('french');
  });

  it('a language the server refused is not applied', async () => {
    apiCall.put.mockResolvedValueOnce({ ok: false, data: null, error: 'refused' });

    expect(await i18n.setLanguage('french')).toBe(false);

    expect(net.frenchLoads).toBe(0);
    expect(i18n.currentLanguage.value).toBe('english');
  });
});

describe('initializeLanguage', () => {
  it('keeps English when the language the server names fails to load', async () => {
    net.frenchFails = true;
    apiCall.get.mockResolvedValueOnce({ ok: true, data: { language: 'french' }, error: null });

    await i18n.initializeLanguage();

    expect(i18n.currentLanguage.value).toBe('english');
    expect(document.documentElement.lang).toBe(bcp47For('english'));
  });
});
