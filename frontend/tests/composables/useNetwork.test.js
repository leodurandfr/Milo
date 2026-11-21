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
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));
// A phone on the AP, never the unit's own screen.
vi.mock('@/utils/kiosk', () => ({ isKiosk: () => false }));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { refreshWifiSignal, handleNetworkStatusChanged, useNetwork } from '@/composables/useNetwork';
import { usePresenceStore } from '@/stores/presenceStore';

const { status } = useNetwork();

function linkEvent({ wifiConnected, ssid, signal }) {
  return {
    data: {
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
