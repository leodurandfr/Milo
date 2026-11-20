// frontend/src/stores/discoveryStore.js
/**
 * Pinia store for wifi-only speaker adoption discovery.
 *
 * Tracks 'Milō' setup hotspots visible from the server and the network the
 * server is on, by name only: an adoption onto it asks the server to read the
 * key itself, so no WiFi key ever reaches this store. The hotspot list is
 * refreshed on demand and via an opt-in 10s polling loop activated while the
 * multiroom discovery UI is open, which skips its ticks while nobody can see
 * the page (hidden tab, kiosk panel asleep): each one may be a WiFi scan on the
 * server. Because the SSID is shared, the list will contain at most one
 * adoptable hotspot at a time.
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useSettingsStore } from '@/stores/settingsStore';

const POLL_INTERVAL_MS = 10000;
// The backend answers an adoption by its ADOPTION_DEADLINE
// (backend/core/multiroom/wifi_adoption.py); this waits past it so that answer,
// a timeout included, is the one the wizard shows. Held above it by
// backend/tests/architecture/test_proxy_timeouts.py.
const ADOPT_TIMEOUT_MS = 120000;

export const useDiscoveryStore = defineStore('discovery', () => {
  const settingsStore = useSettingsStore();

  // === STATE ===

  // Hotspots currently visible from the server (always 'Milō').
  const hotspots = ref([]);

  // True while a hotspot scan is in flight.
  const scanning = ref(false);

  // The network the server is on, or null if not yet loaded.
  // When loaded: { available: true, ssid } | { available: false, ssid: null }
  const serverWifi = ref(null);

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
   * Load the network the server is on, offered as the speaker's target.
   * `available: false` when the server is ethernet-only, or on a network a
   * speaker could not join.
   */
  async function loadServerWifi() {
    const result = await apiCall.get('/api/discovery/server-wifi', {
      category: 'discovery',
      message: 'Error loading the server wifi',
    });
    if (result.ok) {
      const data = result.data.data;
      const available = data?.available === true && !!data.ssid;
      serverWifi.value = { available, ssid: available ? data.ssid : null };
    }
  }

  /**
   * Orchestrate adoption of a wifi-only speaker.
   * Throws on failure so the caller can surface a precise error to the UI.
   * @param {Object} speaker - { ssid, audio_id, speaker_name, speaker_type }
   * @param {Object|null} wifi - { ssid, password } typed by the owner, or null
   *   for the server's own network, whose key the server reads itself
   */
  async function adoptSpeaker(speaker, wifi, { errorRef } = {}) {
    const target = wifi
      ? { wifi_ssid: wifi.ssid, wifi_password: wifi.password }
      : { wifi_ssid: serverWifi.value.ssid, use_server_network: true };
    const payload = { ...speaker, ...target };
    let result;
    try {
      result = await apiCall.post('/api/discovery/adopt-speaker', payload, {
        category: 'discovery',
        message: 'Error adopting wifi speaker',
        timeout: ADOPT_TIMEOUT_MS,
        rethrow: true,
        errorRef,
      });
    } catch (error) {
      // The route refuses with `{code, message}`: the code is what the screen
      // translates, the message the server's own words it folds under it.
      const detail = error?.response?.data?.detail;
      if (errorRef && detail && typeof detail === 'object' && !Array.isArray(detail)) {
        errorRef.value = typeof detail.message === 'string' ? detail.message : null;
      }
      throw error;
    }
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
    pollTick();
    pollTimer = setInterval(pollTick, POLL_INTERVAL_MS);
  }

  function pollTick() {
    if (document.visibilityState !== 'visible' || settingsStore.isScreenSleeping) return;
    scanHotspots();
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
    serverWifi,

    // Actions
    loadServerWifi,
    adoptSpeaker,
    startPolling,
    stopPolling,
  };
});
