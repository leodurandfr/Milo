// frontend/src/stores/systemStore.js
/**
 * Pinia store for system-level state.
 *
 * Currently tracks whether another Milō server has claimed `milo.local`
 * on the local network (mDNS hostname conflict). The backend re-checks
 * every 5 minutes and broadcasts state changes via WebSocket.
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';

export const useSystemStore = defineStore('system', () => {
  const hostnameConflict = ref(false);
  const advertisedName = ref(null);
  const localIp = ref(null);
  const rechecking = ref(false);
  // Default true (fail-open): if the backend hasn't reported yet, don't flash an offline banner.
  const isOnline = ref(true);

  function applyState(state) {
    if (!state) return;
    if (typeof state.hostname_conflict === 'boolean') {
      hostnameConflict.value = state.hostname_conflict;
    }
    if (state.advertised_name !== undefined) {
      advertisedName.value = state.advertised_name;
    }
    if (state.local_ip !== undefined) {
      localIp.value = state.local_ip;
    }
    if (typeof state.online === 'boolean') {
      isOnline.value = state.online;
    }
  }

  async function fetchStatus() {
    const result = await apiCall.get('/api/system/status', {
      category: 'system',
      message: 'Error fetching system status',
      checkStatus: true,
    });
    if (result.ok) {
      applyState(result.data.data);
    }
  }

  async function recheckHostname() {
    if (rechecking.value) return;
    rechecking.value = true;
    try {
      const result = await apiCall.post('/api/system/recheck-hostname', null, {
        category: 'system',
        message: 'Error rechecking hostname',
        checkStatus: true,
      });
      if (result.ok) {
        applyState(result.data.data);
      }
    } finally {
      rechecking.value = false;
    }
  }

  function handleConflictEvent(event) {
    applyState(event?.data);
  }

  function handleConnectivityEvent(event) {
    applyState(event?.data);
  }

  async function resync() {
    return fetchStatus();
  }

  return {
    resync,
    hostnameConflict,
    advertisedName,
    localIp,
    rechecking,
    isOnline,
    fetchStatus,
    recheckHostname,
    handleConflictEvent,
    handleConnectivityEvent,
  };
});
