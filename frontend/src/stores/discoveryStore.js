// frontend/src/stores/discoveryStore.js
/**
 * Pinia store for wifi-only speaker adoption discovery.
 *
 * Tracks 'Milō' setup hotspots visible from the server and the server's own
 * WiFi credentials (used to auto-fill the adoption form). The hotspot list is
 * refreshed on demand and via an opt-in 10s polling loop activated while the
 * multiroom discovery UI is open. Because the SSID is shared, the list will
 * contain at most one adoptable hotspot at a time.
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';

const POLL_INTERVAL_MS = 10000;
// Worst-case server-side adoption: ~30s hotspot connect + ~20s push + ~30s
// restore wifi → ~80s. Add headroom so the request doesn't dangle if a step
// stalls just below its own timeout.
const ADOPT_TIMEOUT_MS = 120000;

export const useDiscoveryStore = defineStore('discovery', () => {
  // === STATE ===

  // Hotspots currently visible from the server (always 'Milō').
  const hotspots = ref([]);

  // True while a hotspot scan is in flight.
  const scanning = ref(false);

  // Server's active wifi credentials, or null if not yet loaded.
  // When loaded: { available: true, ssid, password } | { available: false }
  const serverWifiCreds = ref(null);

  // Polling lifecycle (timer + active subscriber count).
  let pollTimer = null;
  let pollSubscribers = 0;

  // === ACTIONS ===

  /**
   * Refresh the list of visible 'Milō' hotspots.
   * Runs a fresh nmcli scan server-side; takes up to ~15s.
   */
  async function scanHotspots() {
    if (scanning.value) return;
    scanning.value = true;
    try {
      const result = await apiCall.get('/api/discovery/wifi-speakers', {
        category: 'discovery',
        message: 'Error scanning wifi speakers',
      });
      if (result.ok) {
        hotspots.value = result.data.data?.hotspots || [];
      }
    } finally {
      scanning.value = false;
    }
  }

  /**
   * Load the server's active wifi credentials for adoption auto-fill.
   * Returns `{ available: false }` when the server is ethernet-only.
   */
  async function loadServerWifiCreds() {
    const result = await apiCall.get('/api/discovery/server-wifi-creds', {
      category: 'discovery',
      message: 'Error loading server wifi creds',
    });
    if (result.ok) {
      serverWifiCreds.value = result.data.data || { available: false };
    }
  }

  /**
   * Orchestrate adoption of a wifi-only speaker.
   * Throws on failure so the caller can surface a precise error to the UI.
   * @param {Object} payload - { ssid, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password }
   */
  async function adoptSpeaker(payload) {
    const result = await apiCall.post('/api/discovery/adopt-speaker', payload, {
      category: 'discovery',
      message: 'Error adopting wifi speaker',
      timeout: ADOPT_TIMEOUT_MS,
      rethrow: true,
    });
    // Optimistically drop the adopted hotspot from the list — it will reboot
    // and stop broadcasting; a stale entry would only confuse the UI.
    hotspots.value = hotspots.value.filter(h => h.ssid !== payload.ssid);
    return result.data.data;
  }

  /**
   * Start the 10s hotspot polling loop. Reference-counted: each caller must
   * pair with `stopPolling()` so the interval clears once no view depends on
   * fresh hotspot data.
   */
  function startPolling() {
    pollSubscribers += 1;
    if (pollTimer !== null) return;
    // Kick an immediate scan so the UI doesn't wait 10s for the first refresh.
    scanHotspots();
    pollTimer = setInterval(() => {
      scanHotspots();
    }, POLL_INTERVAL_MS);
  }

  /**
   * Decrement the subscriber count and stop polling once no caller remains.
   */
  function stopPolling() {
    if (pollSubscribers > 0) pollSubscribers -= 1;
    if (pollSubscribers > 0) return;
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  return {
    // State
    hotspots,
    serverWifiCreds,

    // Actions
    loadServerWifiCreds,
    adoptSpeaker,
    startPolling,
    stopPolling,
  };
});
