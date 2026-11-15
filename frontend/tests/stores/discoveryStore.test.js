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

/**
 * The server's own network is offered as a speaker's target by name only: the
 * adoption asks the server to read the key itself, so the key never crosses a
 * GET any client of the LAN, or of the open recovery AP, can make. Fails when
 * the store keeps a key, or when an adoption onto the server's network sends
 * one — the backend refuses that body with a 422.
 */
describe('discoveryStore — the server network offered for an adoption', () => {
  const SPEAKER = { ssid: 'Milō', audio_id: 'hifiberry_amp2', speaker_name: 'Bureau', speaker_type: 'bookshelf' };
  let discovery;

  function serverAnswers(data) {
    apiCall.get.mockImplementation(async url => (
      url === '/api/discovery/server-wifi' ? ok({ status: 'success', data }) : ok({ status: 'success', data: {} })
    ));
  }

  function adoptBody() {
    const [[url, body]] = apiCall.post.mock.calls;
    expect(url).toBe('/api/discovery/adopt-speaker');
    return body;
  }

  beforeEach(() => {
    resetApiCallMock();
    setActivePinia(createPinia());
    discovery = useDiscoveryStore();
  });

  it('keeps the name and the availability, and nothing else the answer carries', async () => {
    serverAnswers({ available: true, ssid: 'Maison', password: 's3cret-psk' });

    await discovery.loadServerWifi();

    expect(Object.keys(discovery.serverWifi).sort()).toEqual(['available', 'ssid']);
    expect(JSON.stringify(discovery.serverWifi)).not.toContain('s3cret-psk');
  });

  it('offers no network it could not name', async () => {
    serverAnswers({ available: true, ssid: '' });

    await discovery.loadServerWifi();

    expect(discovery.serverWifi.available).toBe(false);
  });

  it("adopts onto the server's network by its name, with no key in the body", async () => {
    serverAnswers({ available: true, ssid: 'Maison' });
    await discovery.loadServerWifi();

    await discovery.adoptSpeaker(SPEAKER, null);

    const body = adoptBody();
    expect(body.use_server_network).toBe(true);
    expect(body.wifi_ssid).toBe(discovery.serverWifi.ssid);
    expect(body).not.toHaveProperty('wifi_password');
  });

  it('adopts onto a typed network with its typed key, never as the server network', async () => {
    await discovery.adoptSpeaker(SPEAKER, { ssid: 'Grenier', password: 'typed' });

    const body = adoptBody();
    expect([body.wifi_ssid, body.wifi_password]).toEqual(['Grenier', 'typed']);
    expect(body).not.toHaveProperty('use_server_network');
  });
});
