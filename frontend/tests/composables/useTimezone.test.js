// frontend/tests/composables/useTimezone.test.js
/**
 * The one-shot timezone adoption.
 *
 * The image ships `Etc/UTC` and the first non-kiosk browser to open the UI
 * hands the unit its real zone. Both guards below are the difference between
 * "automatic" and "destructive":
 *
 *   * without the `is_default` check, every browser that opens milo.local
 *     rewrites the zone — so a unit set to Europe/Paris flips to whatever a
 *     visitor's phone says, silently, and flips back on the next visit;
 *   * without the kiosk check, the Pi's own touchscreen writes back the value
 *     already in place — harmless, but it is a PUT on every boot forever.
 *
 * Nothing in the app surfaces a failure here (there is no clock and no
 * wall-clock schedule), so a regression would show up only in journal
 * timestamps months later. That is what this file is for.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
vi.mock('@/utils/kiosk', () => ({ isKiosk: vi.fn(() => false) }));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { isKiosk } from '@/utils/kiosk';
import { adoptBrowserTimezone } from '@/composables/useTimezone';

const BROWSER_ZONE = 'Asia/Singapore';

function stubBrowserZone(zone) {
  vi.spyOn(Intl, 'DateTimeFormat').mockReturnValue({
    resolvedOptions: () => ({ timeZone: zone }),
  });
}

function systemTimezone({ timezone, is_default }) {
  return ok({ status: 'success', data: { timezone, is_default, available: [] } });
}

beforeEach(() => {
  resetApiCallMock();
  vi.mocked(isKiosk).mockReturnValue(false);
  stubBrowserZone(BROWSER_ZONE);
});

describe('adoptBrowserTimezone', () => {
  it('hands the unit the browser zone while nobody has chosen one', async () => {
    apiCall.get.mockResolvedValueOnce(systemTimezone({ timezone: 'Etc/UTC', is_default: true }));
    apiCall.put.mockResolvedValueOnce(ok({ status: 'success' }));

    const adopted = await adoptBrowserTimezone();

    expect(adopted).toBe(true);
    expect(apiCall.put).toHaveBeenCalledWith(
      '/api/system/timezone',
      { timezone: BROWSER_ZONE },
      expect.anything()
    );
  });

  it('never overwrites a zone somebody chose', async () => {
    apiCall.get.mockResolvedValueOnce(
      systemTimezone({ timezone: 'Europe/Paris', is_default: false })
    );

    expect(await adoptBrowserTimezone()).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });

  it('says nothing from the kiosk, whose browser is this system', async () => {
    vi.mocked(isKiosk).mockReturnValue(true);

    expect(await adoptBrowserTimezone()).toBe(false);
    expect(apiCall.get).not.toHaveBeenCalled();
  });

  it('leaves the unit alone when the read fails', async () => {
    apiCall.get.mockResolvedValueOnce(fail('backend down'));

    expect(await adoptBrowserTimezone()).toBe(false);
    expect(apiCall.put).not.toHaveBeenCalled();
  });
});
