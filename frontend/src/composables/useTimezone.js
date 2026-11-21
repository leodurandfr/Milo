// frontend/src/composables/useTimezone.js
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';
import { isKiosk } from '@/utils/kiosk';

/**
 * Give the unit its timezone, once, from the screen that is looking at it.
 *
 * The image ships `Etc/UTC` — not a location, a value that means "nobody has
 * told us yet" — and this is what replaces it. A browser knows its own zone
 * exactly, which beats a geo-IP lookup (a VPN or a carrier's egress lies) and
 * costs no third-party call on a unit that has just been unboxed.
 *
 * The kiosk's browser is this system and would only report the value already
 * in place, so the kiosk adopts the zone of the WiFi country the wizard asked
 * for instead (`country_zone`), which the backend names only when that country
 * keeps one clock: in the United States or in Spain a guess would read as a
 * choice, and the next phone could no longer hand the unit its exact zone.
 *
 * One guard keeps this from being magic: the backend must still report
 * `is_default`, so a zone someone chose is never overwritten by whoever next
 * opens the UI. A zone the browser reports under a legacy name (Chromium's
 * `Asia/Calcutta`) is sent as it is: the backend sets the zone it links to.
 * One that names no place (`UTC`, `Etc/GMT-1`) is not sent, since the backend
 * refuses it and the default is already UTC.
 *
 * What depends on the zone: the kiosk's Auto theme, which turns dark from
 * sunset to sunrise at the zone's point and stays light for good on `Etc/UTC`,
 * which has none; the journal's timestamps; and the satellites, which take the
 * server's zone. Not a wizard step, so every failure here is silent, at debug:
 * Settings › Language and region is the correction.
 *
 * Called from App.vue once the initial state is in — on the kiosk, once the
 * unit is set up, since the backend names no country zone while the wizard's
 * guessed country may still be corrected — and never from a client of the
 * recovery access point, where the write would be refused.
 */
export async function adoptTimezone() {
  const current = await apiCall.get('/api/system/timezone', {
    category: 'system',
    message: 'Failed to read the system timezone',
    logLevel: 'debug'
  });
  if (!current.ok || !current.data.data.is_default) return false;

  const zone = isKiosk()
    ? current.data.data.country_zone
    : Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (!namesAPlace(zone) || zone === current.data.data.timezone) return false;

  const applied = await apiCall.put('/api/system/timezone', { timezone: zone }, {
    category: 'system',
    message: 'Failed to adopt a timezone',
    logLevel: 'debug'
  });

  if (applied.ok) {
    logger.info('system', `Timezone adopted from ${isKiosk() ? 'the WiFi country' : 'this browser'}: ${zone}`);
    return true;
  }
  return false;
}

/**
 * A pre-filter only: a zone with no area (`UTC`) or in `Etc` can never name a
 * place, so it is not sent. Whether the unit offers the rest is the backend's
 * to say — PUT resolves a link and refuses what it does not offer.
 */
function namesAPlace(zone) {
  return typeof zone === 'string' && zone.includes('/') && !zone.startsWith('Etc/');
}
