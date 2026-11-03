// frontend/tests/stores/discoveryStore.test.js
/**
 * Settings > Multiroom polls the server for 'Milō' setup hotspots every 10 s
 * while it is open, and every poll can be a WiFi scan on the server. The kiosk
 * keeps the page open with its panel asleep (sleep only turns the backlight
 * off), and a remote tab keeps it open in the background: 7 scans a minute
 * for a screen nobody looks at, on a radio that may carry the multiroom stream.
 * A tick is therefore skipped while the page is hidden or the panel sleeps.
 *
 * The stores are the real ones; HTTP goes through the apiCall mock.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => true }));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { useDiscoveryStore } from '@/stores/discoveryStore';
import { useSettingsStore } from '@/stores/settingsStore';

const SCAN_URL = '/api/discovery/wifi-speakers';

function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
}

function scans() {
  return apiCall.get.mock.calls.filter(([url]) => url === SCAN_URL).length;
}

describe('discoveryStore — the hotspot poll', () => {
  let discovery;
  let settings;

  beforeEach(() => {
    vi.useFakeTimers();
    resetApiCallMock();
    apiCall.get.mockResolvedValue(ok({ status: 'success', data: { hotspots: [] } }));
    setVisibility('visible');
    setActivePinia(createPinia());
    discovery = useDiscoveryStore();
    settings = useSettingsStore();
  });

  afterEach(() => {
    discovery.stopPolling();
    setVisibility('visible');
    vi.useRealTimers();
  });

  it('polling skips ticks while the screen sleeps, and scans again on the tick after it wakes', async () => {
    settings.updateScreenSleeping(true);

    discovery.startPolling();
    await vi.advanceTimersByTimeAsync(30000);
    expect(scans()).toBe(0);

    settings.updateScreenSleeping(false);
    await vi.advanceTimersByTimeAsync(10000);
    expect(scans()).toBe(1);
  });

  it('polling skips ticks while the page is hidden, and scans again on the tick after it shows', async () => {
    setVisibility('hidden');

    discovery.startPolling();
    await vi.advanceTimersByTimeAsync(30000);
    expect(scans()).toBe(0);

    setVisibility('visible');
    await vi.advanceTimersByTimeAsync(10000);
    expect(scans()).toBe(1);
  });

  it('a visible, lit page scans at once and on every tick', async () => {
    discovery.startPolling();
    await vi.advanceTimersByTimeAsync(0);
    expect(scans()).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(scans()).toBe(3);
  });
});
