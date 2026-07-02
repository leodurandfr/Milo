// frontend/src/composables/useNetwork.js
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';

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
 * Pre-load network status for instant rendering when NetworkSettings opens.
 * Call from SettingsModal.onMounted() (non-blocking, like radioStore).
 *
 * force=true refetches even when already loaded — `status_changed` WS deltas
 * (cable/wifi link changes) may have been missed while disconnected/backgrounded,
 * so App.vue::resyncStores() calls this on reconnect/tab-visible.
 */
export async function preloadNetworkStatus({ force = false } = {}) {
  if (_statusLoaded && !force) return;
  const result = await apiCall.get('/api/network/status', {
    category: 'network',
    message: 'Failed to preload network status'
  });
  if (result.ok) {
    _status.value = result.data.data;
    _statusLoaded = true;
  }
}

/**
 * WS handler for `network.status_changed` events broadcast by the backend on
 * physical link changes (cable plug/unplug, WiFi associate/dissociate).
 * Updates the shared module-level status so any mounted NetworkSettings
 * view reflects the new state in real time.
 */
export function handleNetworkStatusChanged(event) {
  if (event?.data) {
    _status.value = event.data;
    _statusLoaded = true;
  }
}

/**
 * Composable for combined Ethernet + WiFi state and API interactions.
 * Shared state is module-level (singleton); UI state is per-instance.
 */
export function useNetwork() {
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
    const result = await apiCall.get('/api/network/status', {
      category: 'network',
      message: 'Failed to load network status'
    });
    if (result.ok) {
      status.value = result.data.data;
      _statusLoaded = true;
    }
  }

  async function loadCountry() {
    const result = await apiCall.get('/api/network/wifi/country', {
      category: 'network',
      message: 'Failed to load WiFi country'
    });
    if (result.ok) {
      _country.value = result.data.data.country_code || '';
      _countryLoaded = true;
    }
  }

  async function setCountry(code) {
    const result = await apiCall.put('/api/network/wifi/country', { country_code: code }, {
      category: 'network',
      message: 'Failed to set WiFi country',
      rethrow: true
    });
    if (result.ok) {
      _country.value = code;
    }
  }

  async function loadSavedNetworks() {
    const result = await apiCall.get('/api/network/wifi/saved', {
      category: 'network',
      message: 'Failed to load saved networks'
    });
    if (result.ok) {
      savedSsids.value = new Set(result.data.data.map(n => n.ssid));
    }
  }

  async function scanNetworks() {
    scanning.value = true;
    const result = await apiCall.get('/api/network/wifi/networks', {
      category: 'network',
      message: 'Failed to scan networks'
    });
    if (result.ok) {
      networks.value = result.data.data;
    }
    scanning.value = false;
  }

  async function connectToNetwork(network, t) {
    connecting.value = true;
    connectError.value = '';
    const payload = { ssid: network.ssid, password: network.security ? password.value : null };
    const result = await apiCall.post('/api/network/wifi/connect', payload, {
      category: 'network',
      message: 'WiFi connection failed'
    });
    if (result.ok) {
      selectedSsid.value = null;
      password.value = '';
      await Promise.all([loadStatus(), scanNetworks(), loadSavedNetworks()]);
    } else {
      connectError.value = result.error?.detail || (t ? t('network.connectFailed') : 'Connection failed');
    }
    connecting.value = false;
  }

  async function saveNetwork(network, t) {
    connecting.value = true;
    connectError.value = '';
    const payload = { ssid: network.ssid, password: network.security ? password.value : null };
    const result = await apiCall.post('/api/network/wifi/save', payload, {
      category: 'network',
      message: 'WiFi save failed'
    });
    if (result.ok) {
      selectedSsid.value = null;
      password.value = '';
      // Update local state to reflect saved SSID without reloading from backend
      status.value = {
        ...status.value,
        wifi: { ...status.value.wifi, saved_ssid: network.ssid },
      };
    } else {
      connectError.value = result.error?.detail || (t ? t('network.saveFailed') : 'Save failed');
    }
    connecting.value = false;
  }

  async function forgetNetwork(ssid) {
    const result = await apiCall.delete(`/api/network/wifi/saved/${encodeURIComponent(ssid)}`, {
      category: 'network',
      message: 'Failed to forget network'
    });
    if (result.ok) {
      savedSsids.value.delete(ssid);
      savedSsids.value = new Set(savedSsids.value);
      await loadStatus();
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
    const result = await apiCall.put('/api/network/wifi/radio', { enabled }, {
      category: 'network',
      message: 'Failed to toggle WiFi'
    });
    if (result.ok) {
      status.value = result.data.data;
      if (enabled) {
        await Promise.all([scanNetworks(), loadSavedNetworks()]);
      }
    } else {
      status.value = previous;
      if (enabled) scanning.value = false;
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
