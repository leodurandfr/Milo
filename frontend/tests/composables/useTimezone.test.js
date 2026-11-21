// frontend/tests/composables/useTimezone.test.js
/**
 * The one-shot timezone adoption.
 *
 * The image ships `Etc/UTC` and the first screen to open the UI hands the unit
 * its real zone: a browser its own, the kiosk its WiFi country's. The guards
 * below are the difference between "automatic" and "destructive", or useless:
 *
 *   * without the `is_default` check, every browser that opens milo.local
 *     rewrites the zone — so a unit set to Europe/Paris flips to whatever a
 *     visitor's phone says, silently, and flips back on the next visit;
 *   * the kiosk's own browser reports this system's zone, so a kiosk that sent
 *     it — or sent nothing — left a unit driven from its screen alone on UTC,
 *     with the Auto theme light all night;
 *   * a legacy name (Chromium's `Asia/Calcutta`) refused here left a browser
 *     in India unable to give the unit any zone at all.
 *
 * A wrong zone shows only in the theme and in journal timestamps months later.
 * That is what this file is for.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
vi.mock('@/utils/kiosk', () => ({ isKiosk: vi.fn(() => false) }));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { isKiosk } from '@/utils/kiosk';
import { adoptTimezone } from '@/composables/useTimezone';

const BROWSER_ZONE = 'Asia/Singapore';

function stubBrowserZone(zone) {
  vi.spyOn(Intl, 'DateTimeFormat').mockReturnValue({
    resolvedOptions: () => ({ timeZone: zone }),
  });
}

// What the backend offers: places as Area/Location only.
const AVAILABLE = [BROWSER_ZONE, 'Asia/Kolkata', 'Europe/Paris'];

function systemTimezone({ timezone, is_default, country_zone = null }) {
  return ok({ status: 'success', data: { timezone, is_default, available: AVAILABLE, country_zone } });
}

function sentZone() {
  return apiCall.put.mock.calls.map(([, body]) => body.timezone);
}

beforeEach(() => {
  resetApiCallMock();
  vi.mocked(isKiosk).mockReturnValue(false);
  stubBrowserZone(BROWSER_ZONE);
  apiCall.put.mockResolvedValue(ok({ status: 'success' }));
});

describe('adoptTimezone', () => {
  it('hands the unit the browser zone while nobody has chosen one', async () => {
    apiCall.get.mockResolvedValueOnce(systemTimezone({ timezone: 'Etc/UTC', is_default: true }));

    expect(await adoptTimezone()).toBe(true);
    expect(sentZone()).toEqual([BROWSER_ZONE]);
  });

  // The unit's list carries `Asia/Kolkata`; the backend sets the zone a legacy
  // name links to, so the name is sent as the browser gave it.
  it('sends a legacy zone name the unit does not list', async () => {
    stubBrowserZone('Asia/Calcutta');
    apiCall.get.mockResolvedValueOnce(systemTimezone({ timezone: 'Etc/UTC', is_default: true }));

    expect(await adoptTimezone()).toBe(true);
    expect(sentZone()).toEqual(['Asia/Calcutta']);
  });

  // A computer kept in UTC or on a fixed offset: nothing a place, and the
  // backend would refuse it every time such a browser opened the UI.
  it.each(['UTC', 'Etc/GMT-1', ''])('sends no zone that names no place (%s)', async (zone) => {
    stubBrowserZone(zone);
    apiCall.get.mockResolvedValueOnce(systemTimezone({ timezone: 'Etc/UTC', is_default: true }));

    expect(await adoptTimezone()).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('never overwrites a zone somebody chose', async () => {
    apiCall.get.mockResolvedValueOnce(
      systemTimezone({ timezone: 'Europe/Paris', is_default: false, country_zone: 'Europe/Paris' })
    );

    expect(await adoptTimezone()).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('gives the kiosk the zone of the WiFi country, never its own browser\'s', async () => {
    vi.mocked(isKiosk).mockReturnValue(true);
    stubBrowserZone('Etc/UTC');
    apiCall.get.mockResolvedValueOnce(
      systemTimezone({ timezone: 'Etc/UTC', is_default: true, country_zone: 'Asia/Kolkata' })
    );

    expect(await adoptTimezone()).toBe(true);
    expect(sentZone()).toEqual(['Asia/Kolkata']);
  });

  // No country yet, or one with several clocks: a guess would read as a choice
  // and stop the next phone from handing the unit its exact zone.
  it('leaves the zone to a phone when the WiFi country names none', async () => {
    vi.mocked(isKiosk).mockReturnValue(true);
    stubBrowserZone(BROWSER_ZONE);
    apiCall.get.mockResolvedValueOnce(systemTimezone({ timezone: 'Etc/UTC', is_default: true }));

    expect(await adoptTimezone()).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('leaves the unit alone when the read fails', async () => {
    apiCall.get.mockResolvedValueOnce(fail('backend down'));

    expect(await adoptTimezone()).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('reports a refused write as no adoption', async () => {
    apiCall.get.mockResolvedValueOnce(systemTimezone({ timezone: 'Etc/UTC', is_default: true }));
    apiCall.put.mockResolvedValueOnce(fail('Unknown timezone'));

    expect(await adoptTimezone()).toBe(false);
  });
});
