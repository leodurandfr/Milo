/**
 * Which Dock › Volume switch a browser obeys (Dock.vue) hangs on this answer: a
 * phone misread as a computer shows or hides the volume by the computer's
 * switch, and the other way round. User agents as the browsers send them.
 */
import { describe, it, expect } from 'vitest';
import { isPhone } from '@/utils/phone';

const PHONES = {
  'Safari, iPhone': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  'Chrome, iPhone': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1',
  'Chrome, Android phone': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36',
  'Firefox, Android phone': 'Mozilla/5.0 (Android 14; Mobile; rv:132.0) Gecko/132.0 Firefox/132.0',
};

const NOT_PHONES = {
  'Safari, Mac': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
  'Chrome, Windows': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  'Chrome, iPad': 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1',
  'Chrome, Android tablet': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  'Milō kiosk (Chromium, Linux)': 'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
};

describe('isPhone', () => {
  it.each(Object.entries(PHONES))('%s is a phone', (_, ua) => {
    expect(isPhone(ua)).toBe(true);
  });

  it.each(Object.entries(NOT_PHONES))('%s is not a phone', (_, ua) => {
    expect(isPhone(ua)).toBe(false);
  });
});
