// frontend/tests/composables/useNetwork.test.js
/**
 * The live WiFi signal read, and the save a client of the recovery AP sends.
 *
 * `refreshWifiSignal()` is polled by whichever view shows the signal arc, and
 * it runs concurrently with the `status_changed` WS events that carry the link
 * state. What breaks if it stops holding: a poll answering after a cable was
 * plugged (or the WiFi dropped) would restore the link state that event just
 * corrected — a settings panel showing "connected" over a dead link, until the
 * next event happens to arrive.
 *
 * `saveNetwork()` is how a server with no screen and no Ethernet is given its
 * new home network from a phone on its AP, which the unit admits only after a
 * gesture on it (428). What breaks if it stops holding: the save fails on the
 * 428, the owner makes the gesture for nothing, and the unit stays on its AP.
 *
 * Every full status feeds `settingsStore.hotspotActive`, which the "WiFi lost"
 * banner, the wizard's save-or-connect choice and the timezone adoption read.
 * What breaks if it stops holding: they keep the value `system/initial_state`
 * gave when the socket opened — the kiosk's never reopens — so the banner never
 * shows on a unit that fell back to its AP, or never leaves once it is off it.
 *
 * `initialize()` is called by a screen and by the network list inside it as
 * both open. What breaks if they stop being one opening: the list's call empties
 * the networks the screen's scan is filling and scans the air a second time, so
 * every opening of Settings › Network or of the wizard's step costs two scans.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
// A phone on the AP, never the unit's own screen.
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => false }));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import {
  refreshWifiSignal, handleNetworkStatusChanged, preloadNetworkStatus, useNetwork,
} from '@/composables/useNetwork';
import { usePresenceStore } from '@/stores/presenceStore';
import { useSettingsStore } from '@/stores/settingsStore';

const { status } = useNetwork();

function linkEvent({ wifiConnected, ssid, signal, hotspotActive = false }) {
  return {
    data: {
      hotspot_active: hotspotActive,
      wifi_enabled: true,
      ethernet: { connected: false, ip_address: null },
      wifi: {
        connected: wifiConnected,
        ssid,
        ip_address: wifiConnected ? '192.168.1.39' : null,
        signal,
        saved_ssid: ssid,
      },
    },
  };
}

describe('refreshWifiSignal', () => {
  beforeEach(() => {
    resetApiCallMock();
    handleNetworkStatusChanged(linkEvent({ wifiConnected: true, ssid: 'Freebox', signal: 61 }));
  });

  it('reads the dedicated endpoint, not the full status', async () => {
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', data: { signal: 48 } }));

    await refreshWifiSignal();

    expect(apiCall.get).toHaveBeenCalledTimes(1);
    expect(apiCall.get.mock.calls[0][0]).toBe('/api/network/wifi/signal');
    expect(status.value.wifi.signal).toBe(48);
  });

  it('patches only the signal, leaving the link state the WS event set', async () => {
    // The poll was in flight while the WiFi dropped; its answer must not
    // resurrect the association.
    apiCall.get.mockResolvedValueOnce(ok({ status: 'success', data: { signal: 55 } }));
    const inFlight = refreshWifiSignal();
    handleNetworkStatusChanged(linkEvent({ wifiConnected: false, ssid: null, signal: null }));
    await inFlight;

    expect(status.value.wifi.connected).toBe(false);
    expect(status.value.wifi.ssid).toBeNull();
    expect(status.value.wifi.ip_address).toBeNull();
    expect(status.value.wifi.signal).toBe(55);
  });

  it('leaves the last known signal alone when the read fails', async () => {
    apiCall.get.mockResolvedValueOnce(fail('offline'));

    await refreshWifiSignal();

    expect(status.value.wifi.signal).toBe(61);
  });
});

describe('saveNetwork over the recovery access point', () => {
  const HEADER = 'X-Milo-Presence';
  const t = (key) => key;
  const network = { ssid: 'Maison', security: 'WPA2' };
  const view = (status) => ({
    id: 'c1', action: 'wifi_save', gesture: 'rotary', status, remaining_s: 120, cooldown_s: 300,
  });
  const challenged = () => fail(
    { message: 'Confirm this on the unit itself', challenge: view('pending'), token: 'secret', header: HEADER },
    428,
  );

  async function flush() {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  }

  beforeEach(() => {
    resetApiCallMock();
    handleNetworkStatusChanged(linkEvent({ wifiConnected: false, ssid: null, signal: null }));
  });

  // How the request is sent again with its token is the presence store's,
  // held by its own test; what is the composable's is reading the 428 as a
  // wait, not as a failed save.
  it('reads the 428 as a wait: once the gesture is made, the network is saved', async () => {
    const presence = usePresenceStore();
    const { saveNetwork, password, connectError } = useNetwork();
    password.value = 'correct horse';
    apiCall.post.mockResolvedValueOnce(challenged()).mockResolvedValueOnce(ok({ status: 'success' }));

    const saving = saveNetwork(network, t);
    await flush();
    presence.handleChallengeEvent({ data: { challenge: view('confirmed') } });
    await saving;

    expect(status.value.wifi.saved_ssid).toBe('Maison');
    expect(connectError.value).toBe('');
  });

  it('says nothing of its own when the gesture never came: the dialog did', async () => {
    const presence = usePresenceStore();
    const { saveNetwork, connectError } = useNetwork();
    apiCall.post.mockResolvedValueOnce(challenged());

    const saving = saveNetwork(network, t);
    await flush();
    presence.handleChallengeEvent({ data: { challenge: null } });
    await saving;

    expect(apiCall.post).toHaveBeenCalledTimes(1);
    expect(status.value.wifi.saved_ssid).toBeNull();
    expect(connectError.value).toBe('');
  });

  it('still says a refused save failed', async () => {
    const { saveNetwork, connectError } = useNetwork();
    apiCall.post.mockResolvedValueOnce(fail('WiFi save failed', 500));

    await saveNetwork(network, t);

    expect(connectError.value).toBe('network.saveFailed');
  });
});

describe('the recovery AP, as every full status says it', () => {
  const onAp = () => linkEvent({ wifiConnected: false, ssid: null, signal: null, hotspotActive: true });
  const offAp = () => linkEvent({ wifiConnected: false, ssid: null, signal: null, hotspotActive: false });

  beforeEach(() => resetApiCallMock());

  it('follows the status event both ways', () => {
    const settings = useSettingsStore();

    handleNetworkStatusChanged(onAp());
    expect(settings.hotspotActive).toBe(true);

    handleNetworkStatusChanged(offAp());
    expect(settings.hotspotActive).toBe(false);
  });

  it('follows the status a resync reads both ways', async () => {
    // A tab back from the background missed the events in between.
    const settings = useSettingsStore();
    apiCall.get
      .mockResolvedValueOnce(ok({ status: 'success', data: onAp().data }))
      .mockResolvedValueOnce(ok({ status: 'success', data: offAp().data }));

    await preloadNetworkStatus({ force: true });
    expect(settings.hotspotActive).toBe(true);

    await preloadNetworkStatus({ force: true });
    expect(settings.hotspotActive).toBe(false);
  });
});

describe('views opening together', () => {
  beforeEach(() => resetApiCallMock());

  const scans = () =>
    apiCall.get.mock.calls.filter(([url]) => url === '/api/network/wifi/networks').length;

  function answerEveryRead() {
    apiCall.get.mockImplementation(async (url) => {
      if (url === '/api/network/wifi/networks') {
        return ok({ status: 'success', data: [{ ssid: 'Freebox', signal: 61, security: 'WPA2', in_use: false }] });
      }
      if (url === '/api/network/wifi/saved') return ok({ status: 'success', data: [] });
      if (url === '/api/network/wifi/country') return ok({ status: 'success', data: { country_code: 'FR' } });
      return ok({ status: 'success', data: linkEvent({ wifiConnected: false, ssid: null, signal: null }).data });
    });
  }

  it('scan the air once, and each is told when it is done', async () => {
    answerEveryRead();
    const screen = useNetwork();
    const list = useNetwork();

    await Promise.all([screen.initialize(), list.initialize()]);

    expect(scans()).toBe(1);
    expect(screen.loading.value).toBe(false);
    expect(list.loading.value).toBe(false);
    expect(list.networks.value.map(n => n.ssid)).toEqual(['Freebox']);
  });

  it('leave the next opening its own scan', async () => {
    answerEveryRead();

    await useNetwork().initialize();
    await useNetwork().initialize();

    expect(scans()).toBe(2);
  });
});
