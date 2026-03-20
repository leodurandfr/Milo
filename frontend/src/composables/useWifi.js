// frontend/src/composables/useWifi.js
import { ref, computed } from 'vue';
import axios from 'axios';
import { logger } from '@/services/logger';

/**
 * Module-level singleton state (persists across component instances).
 * Same pattern as Pinia stores: data survives mount/unmount cycles.
 */
const _status = ref({
  wifi_enabled: true,
  ethernet: { connected: false, ip_address: null },
  wifi: { connected: false, ssid: null, ip_address: null, signal: null, saved_ssid: null },
});
const _networks = ref([]);
const _savedSsids = ref(new Set());
const _scanning = ref(false);
const _country = ref('');
let _statusLoaded = false;
let _countryLoaded = false;

/**
 * Pre-load wifi status for instant rendering when NetworkSettings opens.
 * Call from SettingsModal.onMounted() (non-blocking, like radioStore).
 */
export async function preloadWifiStatus() {
  if (_statusLoaded) return;
  try {
    const res = await axios.get('/api/wifi/status');
    _status.value = res.data.data;
    _statusLoaded = true;
  } catch (error) {
    logger.error('wifi', 'Failed to preload network status', error);
  }
}

/**
 * Composable for WiFi state and API interactions.
 * Shared state is module-level (singleton); UI state is per-instance.
 */
export function useWifi() {
  // Shared state (module-level singleton)
  const status = _status;
  const networks = _networks;
  const savedSsids = _savedSsids;
  const scanning = _scanning;

  // Per-instance UI state
  const loading = ref(!_statusLoaded);
  const connecting = ref(false);
  const connectError = ref('');
  const selectedSsid = ref(null);
  const password = ref('');

  const preferredSsid = computed(() =>
    status.value.wifi.ssid || status.value.wifi.saved_ssid
  );

  const preferredNetwork = computed(() => {
    const ssid = preferredSsid.value;
    if (!ssid) return null;
    const fromScan = networks.value.find(n => n.ssid === ssid);
    if (fromScan) return fromScan;
    // Not in scan results (out of range) but still saved
    return { ssid, signal: null, security: '', in_use: false };
  });

  const otherNetworks = computed(() => {
    const pref = preferredSsid.value;
    return networks.value.filter(n => !n.in_use && n.ssid !== pref);
  });

  function selectNetwork(network) {
    if (network.in_use) return;
    if (selectedSsid.value === network.ssid) {
      selectedSsid.value = null;
      password.value = '';
      connectError.value = '';
    } else {
      selectedSsid.value = network.ssid;
      password.value = '';
      connectError.value = '';
    }
  }

  async function loadStatus() {
    try {
      const res = await axios.get('/api/wifi/status');
      status.value = res.data.data;
      _statusLoaded = true;
    } catch (error) {
      logger.error('wifi', 'Failed to load network status', error);
    }
  }

  async function loadCountry() {
    try {
      const res = await axios.get('/api/wifi/country');
      _country.value = res.data.data.country_code || '';
      _countryLoaded = true;
    } catch (error) {
      logger.error('wifi', 'Failed to load WiFi country', error);
    }
  }

  async function setCountry(code) {
    try {
      await axios.put('/api/wifi/country', { country_code: code });
      _country.value = code;
    } catch (error) {
      logger.error('wifi', 'Failed to set WiFi country', error);
      throw error;
    }
  }

  async function loadSavedNetworks() {
    try {
      const res = await axios.get('/api/wifi/saved');
      savedSsids.value = new Set(res.data.data.map(n => n.ssid));
    } catch (error) {
      logger.error('wifi', 'Failed to load saved networks', error);
    }
  }

  async function scanNetworks() {
    scanning.value = true;
    try {
      const res = await axios.get('/api/wifi/networks');
      networks.value = res.data.data;
    } catch (error) {
      logger.error('wifi', 'Failed to scan networks', error);
    } finally {
      scanning.value = false;
    }
  }

  async function connectToNetwork(network, t) {
    connecting.value = true;
    connectError.value = '';
    try {
      const payload = { ssid: network.ssid, password: network.security ? password.value : null };
      await axios.post('/api/wifi/connect', payload);
      selectedSsid.value = null;
      password.value = '';
      await Promise.all([loadStatus(), scanNetworks(), loadSavedNetworks()]);
    } catch (error) {
      const detail = error.response?.data?.detail;
      connectError.value = detail || (t ? t('network.connectFailed') : 'Connection failed');
      logger.error('wifi', 'WiFi connection failed', error);
    } finally {
      connecting.value = false;
    }
  }

  async function saveNetwork(network, t) {
    connecting.value = true;
    connectError.value = '';
    try {
      const payload = { ssid: network.ssid, password: network.security ? password.value : null };
      await axios.post('/api/wifi/save', payload);
      selectedSsid.value = null;
      password.value = '';
      // Update local state to reflect saved SSID without reloading from backend
      status.value = {
        ...status.value,
        wifi: { ...status.value.wifi, saved_ssid: network.ssid },
      };
    } catch (error) {
      const detail = error.response?.data?.detail;
      connectError.value = detail || (t ? t('network.saveFailed') : 'Save failed');
      logger.error('wifi', 'WiFi save failed', error);
    } finally {
      connecting.value = false;
    }
  }

  async function forgetNetwork(ssid) {
    try {
      await axios.delete(`/api/wifi/saved/${encodeURIComponent(ssid)}`);
      savedSsids.value.delete(ssid);
      savedSsids.value = new Set(savedSsids.value);
      await loadStatus();
    } catch (error) {
      logger.error('wifi', 'Failed to forget network', error);
    }
  }

  // Cache SSID across disable/enable for optimistic connection card display
  let cachedSsid = null;

  async function toggleWifi(enabled) {
    // Optimistic update for immediate UI response
    const previous = { ...status.value };
    if (!enabled) {
      cachedSsid = status.value.wifi.ssid || status.value.wifi.saved_ssid;
    }
    if (enabled && cachedSsid) {
      // Restore cached SSID so connection card appears with ToggleSection
      status.value = {
        ...status.value,
        wifi_enabled: true,
        wifi: { ...status.value.wifi, saved_ssid: cachedSsid },
      };
    } else {
      status.value = { ...status.value, wifi_enabled: enabled };
    }
    if (!enabled) {
      networks.value = [];
    }
    if (enabled) {
      scanning.value = true;
    }
    try {
      const res = await axios.put('/api/wifi/radio', { enabled });
      status.value = res.data.data;
      if (enabled) {
        await Promise.all([scanNetworks(), loadSavedNetworks()]);
      }
    } catch (error) {
      status.value = previous;
      if (enabled) scanning.value = false;
      logger.error('wifi', 'Failed to toggle WiFi', error);
    }
  }

  async function initialize() {
    try {
      const tasks = [loadSavedNetworks(), scanNetworks()];
      if (!_statusLoaded) tasks.push(loadStatus());
      if (!_countryLoaded) tasks.push(loadCountry());
      await Promise.all(tasks);
    } finally {
      loading.value = false;
    }
  }

  return {
    status,
    networks,
    savedSsids,
    country: _country,
    loading,
    scanning,
    connecting,
    connectError,
    selectedSsid,
    password,
    preferredSsid,
    preferredNetwork,
    otherNetworks,
    selectNetwork,
    loadStatus,
    loadSavedNetworks,
    scanNetworks,
    connectToNetwork,
    saveNetwork,
    forgetNetwork,
    toggleWifi,
    setCountry,
    initialize,
  };
}
