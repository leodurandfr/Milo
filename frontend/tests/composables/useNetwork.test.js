// frontend/tests/composables/useNetwork.test.js
/**
 * The live WiFi signal read.
 *
 * `refreshWifiSignal()` is polled by whichever view shows the signal arc, and
 * it runs concurrently with the `status_changed` WS events that carry the link
 * state. What breaks if it stops holding: a poll answering after a cable was
 * plugged (or the WiFi dropped) would restore the link state that event just
 * corrected — a settings panel showing "connected" over a dead link, until the
 * next event happens to arrive.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';
import { refreshWifiSignal, handleNetworkStatusChanged, useNetwork } from '@/composables/useNetwork';

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
