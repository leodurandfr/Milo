// frontend/src/utils/backendDown.js
/**
 * When a page gives the backend up for the rescue page, and what that page says.
 *
 * The rescue page (`milo-down.html`, the second Vite entry) is static: nginx
 * serves it without the backend, and answers 502, 503 or 504 itself, with that
 * page as the body, whenever it cannot reach the backend
 * (rootfs/etc/nginx/sites-enabled/milo, `error_page`). So one of those statuses
 * means the unit is up and Milō is not. A request with no answer at all means
 * the unit itself is out of reach — a page then stays where it is, since the
 * rescue page could not load either.
 */
import { bcp47For } from '@/constants/countries';

export const RESCUE_PAGE = '/milo-down.html';

const NGINX_FALLBACK_STATUSES = new Set([502, 503, 504]);

/**
 * How long the page says "restarting" before it says "no longer starts". Past
 * a cold boot on an SD card (about a minute) many times over, and short of the
 * two hung starts a release on trial is given (TimeoutStartSec=180 each) before
 * its guard runs the previous one — a power cut there is one the update
 * journal resumes from.
 */
export const STUCK_AFTER_MS = 5 * 60 * 1000;

/** How often the rescue page asks whether Milō is back. */
export const PROBE_INTERVAL_MS = 3000;

/**
 * Whether a failed request's status is nginx saying the backend is not there.
 *
 * @param {number|null|undefined} status - `error.status` of an apiCall result
 */
export function isBackendDown(status) {
  return NGINX_FALLBACK_STATUSES.has(status);
}

// When this tab first found Milō gone, in sessionStorage: a backend that
// listens for a few seconds before it dies again sends the page back to the
// app, which leaves for it again, and a clock started with each page would
// never reach STUCK_AFTER_MS. Cleared by the app at its first initial state.
const RESCUE_SINCE_KEY = 'milo_rescue_since';

/**
 * What the rescue page says `elapsedMs` after the tab first found Milō gone
 * (or last found the unit out of reach, `rescueSinceAfter`), its last probe
 * answered `status`. "No longer starts" only while nginx is
 * the one answering: no answer at all is the unit itself out of reach — off,
 * or rebooting —, which a reflash would not fix.
 *
 * @param {number} elapsedMs
 * @param {number|null|undefined} status - the last probe's `error.status`
 */
export function rescuePhase(elapsedMs, status) {
  return elapsedMs >= STUCK_AFTER_MS && isBackendDown(status) ? 'stuck' : 'restarting';
}

/**
 * When this tab first found Milō gone: kept from an earlier visit of the page,
 * else `now`, kept for the next one. A storage that refuses (a private window
 * may) starts the clock with the page.
 */
export function rescueSince(now) {
  try {
    const kept = Number(sessionStorage.getItem(RESCUE_SINCE_KEY));
    if (kept > 0 && kept <= now) return kept;
    sessionStorage.setItem(RESCUE_SINCE_KEY, String(now));
  } catch {
    // The clock starts with this page.
  }
  return now;
}

/**
 * When the outage counts from after a probe answered `status`. No answer at
 * all is the unit out of reach — off, rebooting, the power cycle the page
 * itself asks for —, so whatever follows is a new start: the clock starts over
 * at `now` (and is kept for the next visit), or a normal boot's first 502s
 * would say "no longer starts" right after the owner followed that advice.
 *
 * @param {number} since - when the clock started
 * @param {number} now
 * @param {number|null|undefined} status - the probe's `error.status`
 */
export function rescueSinceAfter(since, now, status) {
  if (status != null) return since;
  try {
    sessionStorage.setItem(RESCUE_SINCE_KEY, String(now));
  } catch {
    // The clock starts over with this page only.
  }
  return now;
}

/** Milō answered: the next time it is gone is a new outage. */
export function clearRescueSince() {
  try {
    sessionStorage.removeItem(RESCUE_SINCE_KEY);
  } catch {
    // Nothing was kept.
  }
}

/**
 * The rescue page, told the language the page leaving for it was in — the
 * backend that holds the setting is the one not answering.
 *
 * @param {string|null} language - a Milō language code, or null when unknown
 */
export function rescuePageUrl(language) {
  return language ? `${RESCUE_PAGE}?lang=${encodeURIComponent(language)}` : RESCUE_PAGE;
}

/**
 * The language the rescue page speaks: the one it was handed, else the first
 * of the browser's it has strings for, else English.
 *
 * @param {string} search - `location.search`
 * @param {readonly string[]} browserLanguages - `navigator.languages`
 * @param {string[]} available - the Milō language codes (i18n)
 */
export function rescueLanguage(search, browserLanguages, available) {
  const asked = new URLSearchParams(search).get('lang');
  if (available.includes(asked)) return asked;
  const primary = (tag) => tag.toLowerCase().split('-')[0];
  for (const tag of browserLanguages ?? []) {
    const match = available.find((code) => primary(bcp47For(code)) === primary(tag));
    if (match) return match;
  }
  return 'english';
}
