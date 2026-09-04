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
  // NetworkManager's connectivity level, kept whole: 'unknown' | 'none' |
  // 'portal' | 'limited' | 'full'. 'unknown' is the fail-open default (backend
  // silent, or NM has not probed yet) and reads as "no problem observed".
  // Whether a *source* is blocked by it is not decided here — the backend
  // crosses the level with the source's own requirement and publishes the
  // answer as full_state.network_unavailable.
  const connectivity = ref('unknown');
  // The label of the audio card hardware.json names when ALSA cannot see it,
  // null when all is well. A HAT is not hot-pluggable, so this is settled at
  // boot and arrives with the status read rather than as a WS delta.
  const audioCardMissing = ref(null);

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
    if (typeof state.connectivity === 'string') {
      connectivity.value = state.connectivity;
    }
    if (state.audio_card_missing !== undefined) {
      audioCardMissing.value = state.audio_card_missing;
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
    connectivity,
    audioCardMissing,
    fetchStatus,
    recheckHostname,
    handleConflictEvent,
    handleConnectivityEvent,
  };
});
