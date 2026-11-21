// frontend/tests/pure/backendDown.test.js
/**
 * What the rescue page says and in which language. The backend that holds the
 * language setting is the one not answering, so the page is told it by the app
 * leaving for it, else guesses it from the browser: a wrong guess is a page in
 * a language the owner cannot read, on the one screen meant to help them.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  STUCK_AFTER_MS, clearRescueSince, rescueLanguage, rescuePageUrl, rescuePhase, rescueSince,
  rescueSinceAfter,
} from '@/utils/backendDown';
import { i18n } from '@/services/i18n';

const CODES = i18n.getAvailableLanguages().map(({ code }) => code);
const searchOf = (url) => new URL(url, 'http://milo.local').search;

describe('rescueLanguage', () => {
  it('speaks the language the app left in', () => {
    for (const code of CODES) {
      expect(rescueLanguage(searchOf(rescuePageUrl(code)), ['en-US'], CODES)).toBe(code);
    }
  });

  it('ignores a language it has no strings for', () => {
    expect(rescueLanguage('?lang=klingon', ['de-AT'], CODES)).toBe('german');
  });

  it('takes the first browser language it has strings for, by its primary subtag', () => {
    expect(rescueLanguage(searchOf(rescuePageUrl(null)), ['ja-JP', 'pt-BR', 'fr'], CODES)).toBe('portuguese');
    expect(rescueLanguage('', ['zh-Hant-TW'], CODES)).toBe('chinese');
  });

  it('falls back to English', () => {
    expect(rescueLanguage('', ['ja-JP'], CODES)).toBe('english');
    expect(rescueLanguage('', undefined, CODES)).toBe('english');
  });
});

describe('rescuePhase', () => {
  it('says restarting until the delay, and no longer starts from it on while nginx answers', () => {
    expect(rescuePhase(0, 502)).toBe('restarting');
    expect(rescuePhase(STUCK_AFTER_MS - 1, 503)).toBe('restarting');
    expect(rescuePhase(STUCK_AFTER_MS, 504)).toBe('stuck');
  });

  it('never says no longer starts of a unit out of reach', () => {
    // No answer at all: the unit is off or rebooting, which a reflash does not fix.
    expect(rescuePhase(STUCK_AFTER_MS * 10, null)).toBe('restarting');
    expect(rescuePhase(STUCK_AFTER_MS * 10, undefined)).toBe('restarting');
  });
});

describe('rescueSince', () => {
  beforeEach(() => clearRescueSince());

  it('keeps the first failure across the page sending the tab back to the app', () => {
    // A backend that listens for seconds, then dies: page, app, page again.
    const first = rescueSince(1_000);
    expect(rescueSince(1_000 + STUCK_AFTER_MS)).toBe(first);
    expect(rescuePhase(1_000 + STUCK_AFTER_MS - first, 502)).toBe('stuck');
  });

  it('starts over after the unit was out of reach, so the boot that follows is not stuck', () => {
    // Stuck, then the owner power-cycles the unit: no answer while it is off,
    // then nginx's 502s while the backend boots.
    let since = rescueSince(0);
    expect(rescuePhase(STUCK_AFTER_MS + 1_000 - since, 502)).toBe('stuck');
    since = rescueSinceAfter(since, STUCK_AFTER_MS + 2_000, 502);
    expect(rescuePhase(STUCK_AFTER_MS + 3_000 - since, 502)).toBe('stuck');
    since = rescueSinceAfter(since, STUCK_AFTER_MS + 4_000, null);
    expect(rescuePhase(STUCK_AFTER_MS + 64_000 - since, 502)).toBe('restarting');
    // Kept across a bounce to the app and back, like the first one.
    expect(rescueSince(STUCK_AFTER_MS + 70_000)).toBe(since);
    expect(rescuePhase(2 * STUCK_AFTER_MS + 4_000 - since, 503)).toBe('stuck');
  });

  it('starts over once the app had Milō answer', () => {
    rescueSince(1_000);
    clearRescueSince();
    expect(rescueSince(50_000)).toBe(50_000);
  });
});
