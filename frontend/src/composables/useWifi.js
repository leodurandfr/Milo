// frontend/src/composables/useWifi.js
import { ref, computed } from 'vue';
import axios from 'axios';
import { logger } from '@/services/logger';

/**
 * Composable for WiFi state and API interactions.
 * Each component instance gets its own state (not a singleton).
 */
export function useWifi() {
  const status = ref({ connected: false, ssid: null, ip_address: null, signal: null, saved_ssid: null });
  const networks = ref([]);
  const savedSsids = ref(new Set());
  const loading = ref(true);
  const scanning = ref(false);
  const connecting = ref(false);
  const connectError = ref('');
  const selectedSsid = ref(null);
  const password = ref('');

  const knownNetworks = computed(() =>
    networks.value.filter(n => n.in_use || savedSsids.value.has(n.ssid))
  );

  const otherNetworks = computed(() =>
    networks.value.filter(n => !n.in_use && !savedSsids.value.has(n.ssid))
  );

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
    } catch (error) {
      logger.error('wifi', 'Failed to load WiFi status', error);
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
      connectError.value = detail || (t ? t('wifi.connectFailed') : 'Connection failed');
      logger.error('wifi', 'WiFi connection failed', error);
    } finally {
      connecting.value = false;
    }
  }

  async function forgetNetwork(ssid) {
    try {
      await axios.delete(`/api/wifi/saved/${encodeURIComponent(ssid)}`);
      savedSsids.value.delete(ssid);
      savedSsids.value = new Set(savedSsids.value);
    } catch (error) {
      logger.error('wifi', 'Failed to forget network', error);
    }
  }

  async function initialize() {
    try {
      await Promise.all([loadStatus(), loadSavedNetworks(), scanNetworks()]);
    } finally {
      loading.value = false;
    }
  }

  return {
    status,
    networks,
    savedSsids,
    loading,
    scanning,
    connecting,
    connectError,
    selectedSsid,
    password,
    knownNetworks,
    otherNetworks,
    selectNetwork,
    loadStatus,
    loadSavedNetworks,
    scanNetworks,
    connectToNetwork,
    forgetNetwork,
    initialize,
  };
}
