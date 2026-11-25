// frontend/src/composables/useNetwork.js
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useSettingsStore } from '@/stores/settingsStore';
import { isKiosk } from '@/utils/kiosk';

/**
 * Module-level singleton state (persists across component instances).
 * Same pattern as Pinia stores: data survives mount/unmount cycles.
 */
const _status = ref({
  wifi_enabled: true,
  ethernet: { connected: false, ip_address: null },
  wifi: { connected: false, connecting: false, ssid: null, ip_address: null, signal: null, saved_ssid: null },
  hotspot_active: false,
  link_lost: false,
});
const _networks = ref([]);
const _savedSsids = ref(new Set());
const _scanning = ref(false);
const _country = ref('');
let _statusLoaded = false;
let _countryLoaded = false;
// The opening in flight, which a second view opening with it joins.
let _opening = null;

/**
 * A whole status the backend sent. `hotspot_active` is the recovery AP, and
 * `link_lost` says the unit lost its network (the AP rose and no link came
 * back since), both held by settingsStore for the app (the wizard's save, the
 * "WiFi lost" banner): `system/initial_state` carries the AP only when the
 * socket opens, and the kiosk's socket outlives every network change, so the
 * status keeps them current.
 */
function _setStatus(next) {
  _status.value = next;
  _statusLoaded = true;
  const settings = useSettingsStore();
  settings.updateHotspotActive(next.hotspot_active);
  settings.updateLinkLost(next.link_lost);
}

/**
 * Pre-load network status for instant rendering when NetworkSettings opens.
 * Call from SettingsModal.onMounted() (non-blocking, like radioStore).
 *
 * force=true refetches even when already loaded — `status_changed` WS deltas
 * (cable/wifi link changes) may have been missed while disconnected/backgrounded,
 * so App.vue::resyncStores() calls this on reconnect/tab-visible.
 */
export async function preloadNetworkStatus({ force = false } = {}) {
  if (_statusLoaded && !force) return true;
  const result = await apiCall.get('/api/network/status', {
    category: 'network',
    message: 'Failed to preload network status'
  });
  if (result.ok) _setStatus(result.data.data);
  return result.ok;
}

/**
 * Refresh only the WiFi signal, from the one endpoint that reads it without
 * rebuilding the whole status server-side. Driven by whichever view is showing
 * the signal arc, for exactly as long as it shows it: the backend does not push
 * the RSSI, so an unread arc costs nothing anywhere.
 *
 * Patches the single field rather than replacing `_status`: a poll in flight
 * across a `status_changed` (a cable being plugged) must not restore the link
 * state that event just corrected.
 */
export async function refreshWifiSignal() {
  const result = await apiCall.get('/api/network/wifi/signal', {
    category: 'network',
    message: 'Failed to read WiFi signal',
    logLevel: 'debug',
  });
  if (!result.ok) return;
  _status.value = {
    ..._status.value,
    wifi: { ..._status.value.wifi, signal: result.data.data.signal },
  };
}

/**
 * WS handler for `network.status_changed` events broadcast by the backend on
 * physical link changes (cable plug/unplug, WiFi associate/dissociate).
 * Updates the shared module-level status so any mounted NetworkSettings
 * view reflects the new state in real time.
 */
export function handleNetworkStatusChanged(event) {
  if (event?.data) _setStatus(event.data);
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
  // The server's detail for the failure connectError says, folded under it.
  const connectDetail = ref(null);
  // The server's detail when the WiFi switch or a Forget was refused.
  const wifiRefusal = ref(null);
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

  // A browser on the unit's recovery access point saves a network rather than
  // joining it: joining takes the one radio from under that browser, and the
  // unit refuses it. The kiosk, a loopback client, joins at once. The store is
  // read in the getter, since the composable may be called at module scope,
  // before any Pinia is active.
  const overRecoveryAp = computed(() => useSettingsStore().hotspotActive && !isKiosk());

  // Settings › Network draws the preferred network in a row of its own, whose
  // one action is Forget, and leaves it out of the list below. While the
  // recovery AP is up the network is the one the unit lost — the "WiFi lost"
  // banner sends every reader, the kiosk's included, to type its key again —
  // so the list keeps it, the only place a key is typed. Only a client of the
  // AP loses the Forget, which RecoveryAccessPointGuard refuses it.
  const preferredForgettable = computed(() => !overRecoveryAp.value);
  const listExcludedSsid = computed(() =>
    useSettingsStore().hotspotActive ? null : (preferredNetwork.value?.ssid ?? null)
  );

  function selectNetwork(network) {
    if (network.in_use) return;
    if (selectedSsid.value === network.ssid) {
      selectedSsid.value = null;
      password.value = '';
      connectError.value = '';
      connectDetail.value = null;
    } else {
      selectedSsid.value = network.ssid;
      password.value = '';
      connectError.value = '';
      connectDetail.value = null;
    }
  }

  async function loadStatus() {
    const result = await apiCall.get('/api/network/status', {
      category: 'network',
      message: 'Failed to load network status'
    });
    if (result.ok) _setStatus(result.data.data);
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

  // A refusal leaves the country where it was and hands the server's detail to
  // `errorRef`; the answer is the verdict, never a throw.
  async function setCountry(code, { errorRef } = {}) {
    const result = await apiCall.put('/api/network/wifi/country', { country_code: code }, {
      category: 'network',
      message: 'Failed to set WiFi country',
      errorRef
    });
    if (result.ok) {
      _country.value = code;
    }
    return result.ok;
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
    connectDetail.value = null;
    const payload = { ssid: network.ssid, password: network.security ? password.value : null };
    const result = await apiCall.post('/api/network/wifi/connect', payload, {
      category: 'network',
      message: 'WiFi connection failed',
      errorRef: connectDetail
    });
    if (result.ok) {
      selectedSsid.value = null;
      password.value = '';
      await Promise.all([loadStatus(), scanNetworks(), loadSavedNetworks()]);
    } else {
      connectError.value = t('network.connectFailed');
    }
    connecting.value = false;
  }

  // What a client of the recovery access point sends: joining would take the
  // one radio from under it (backend/api/middleware.py::RecoveryAccessPointGuard).
  // `tryNow`: over the AP once the unit is set up (Settings › Network), the
  // unit tries the network seconds after answering — the wizard saves, then
  // goes on over the AP, and leaves it off.
  async function saveNetwork(network, t, { tryNow = false } = {}) {
    connecting.value = true;
    connectError.value = '';
    connectDetail.value = null;
    const payload = {
      ssid: network.ssid,
      password: network.security ? password.value : null,
      try_now: tryNow,
    };
    const result = await apiCall.post('/api/network/wifi/save', payload, {
      category: 'network',
      message: 'WiFi save failed',
      errorRef: connectDetail
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
      connectError.value = t('network.saveFailed');
    }
    connecting.value = false;
    return result.ok;
  }

  async function forgetNetwork(ssid) {
    wifiRefusal.value = null;
    const result = await apiCall.delete(`/api/network/wifi/saved/${encodeURIComponent(ssid)}`, {
      category: 'network',
      message: 'Failed to forget network',
      errorRef: wifiRefusal
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
    // Emptied as the section opens, not as it closes: the list stays drawn
    // while the section fades out, and the skeletons stand in for the rescan.
    if (enabled) {
      networks.value = [];
      scanning.value = true;
    }
    wifiRefusal.value = null;
    const result = await apiCall.put('/api/network/wifi/radio', { enabled }, {
      category: 'network',
      message: 'Failed to toggle WiFi',
      errorRef: wifiRefusal
    });
    if (result.ok) {
      _setStatus(result.data.data);
      if (enabled) {
        await Promise.all([scanNetworks(), loadSavedNetworks()]);
      }
    } else {
      status.value = previous;
      if (enabled) scanning.value = false;
    }
  }

  // Each opening reads the air afresh: the last scan is dropped, so the list
  // shows the skeletons until the new one lands instead of a stale list. Views
  // opening together are one opening — Settings › Network and the list inside
  // it, the wizard's step and its list — so the second joins the first rather
  // than emptying the list it is filling and scanning a second time.
  async function initialize() {
    try {
      if (!_opening) {
        networks.value = [];
        const tasks = [loadSavedNetworks(), scanNetworks()];
        if (!_statusLoaded) tasks.push(loadStatus());
        if (!_countryLoaded) tasks.push(loadCountry());
        _opening = Promise.all(tasks).finally(() => { _opening = null; });
      }
      await _opening;
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
    connectDetail,
    wifiRefusal,
    selectedSsid,
    password,
    preferredSsid,
    preferredNetwork,
    overRecoveryAp,
    preferredForgettable,
    listExcludedSsid,
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
