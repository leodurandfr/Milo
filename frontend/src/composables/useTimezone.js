// frontend/src/composables/useTimezone.js
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';
import { isKiosk } from '@/utils/kiosk';

/**
 * Give the unit its timezone, once, from the browser that is looking at it.
 *
 * The image ships `Etc/UTC` — not a location, a value that means "nobody has
 * told us yet" — and this is what replaces it. A browser knows its own zone
 * exactly, which beats a geo-IP lookup (a VPN or a carrier's egress lies) and
 * costs no third-party call on a unit that has just been unboxed.
 *
 * Two guards, and they are what keep this from being magic:
 *
 *   * the backend must still report `is_default`, so a zone someone chose is
 *     never overwritten by whoever next opens the UI;
 *   * the kiosk is skipped, because its browser *is* this system and would
 *     only ever report the value already in place.
 *
 * It is deliberately not a wizard step. Nothing in Milō breaks on a wrong zone
 * — there is no clock and no wall-clock schedule, only journal timestamps and
 * podcast "today/yesterday" — so it does not earn a question, and every
 * failure here is silent by design. Settings > Language and region is the
 * correction.
 *
 * Called from App.vue after the stores resync.
 */
export async function adoptBrowserTimezone() {
  if (isKiosk()) return false;

  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!browserZone) return false;

  const current = await apiCall.get('/api/system/timezone', {
    category: 'system',
    message: 'Failed to read the system timezone',
    logLevel: 'debug'
  });
  if (!current.ok || !current.data.data.is_default) return false;
  if (browserZone === current.data.data.timezone) return false;

  const applied = await apiCall.put('/api/system/timezone', { timezone: browserZone }, {
    category: 'system',
    message: 'Failed to adopt the browser timezone',
    logLevel: 'debug'
  });

  if (applied.ok) {
    logger.info('system', `Timezone adopted from this browser: ${browserZone}`);
    return true;
  }
  return false;
}
