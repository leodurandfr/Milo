// frontend/src/stores/systemStore.js
/**
 * Pinia store for system-level state.
 *
 * Tracks whether another Milō server has claimed `milo.local` on the local
 * network (mDNS hostname conflict) — the backend re-checks every 5 minutes and
 * broadcasts state changes via WebSocket — and the SSH / account password
 * panel of Réglages › Système.
 */
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';

export const useSystemStore = defineStore('system', () => {
  const hostnameConflict = ref(false);
  const advertisedName = ref(null);
  const localIp = ref(null);
  const rechecking = ref(false);
  // The label of the audio card hardware.json names when ALSA cannot see it,
  // null when all is well. A HAT is not hot-pluggable, so this is settled at
  // boot and arrives with the status read rather than as a WS delta.
  const audioCardMissing = ref(null);

  // One panel spread over two views, System and its password sub-view, so it
  // lives here rather than in either: a password save outlives the view that
  // started it, and going back mid-save mounts System, whose read would
  // otherwise answer from before the save and keep the factory warning up.
  const ssh = ref({ enabled: false, active: false, passwordIsDefault: true });
  let passwordSave = null;

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
    return result.ok;
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

  function applySsh(data) {
    ssh.value = {
      enabled: !!data.enabled,
      active: !!data.active,
      passwordIsDefault: !!data.password_is_default,
    };
  }

  async function loadSsh() {
    await passwordSave;
    const result = await apiCall.get('/api/system/ssh', {
      category: 'system',
      message: 'Failed to load SSH state',
    });
    if (result.ok) applySsh(result.data.data);
    return result.ok;
  }

  async function setSshEnabled(enabled, { errorRef } = {}) {
    const result = await apiCall.put('/api/system/ssh', { enabled }, {
      category: 'system',
      message: 'Failed to change SSH state',
      errorRef,
    });
    if (result.ok) {
      applySsh(result.data.data);
    } else {
      // The switch never moved server-side; re-read rather than trust the click.
      await loadSsh();
    }
    return result.ok;
  }

  async function setPassword(password, { errorRef } = {}) {
    passwordSave = apiCall.post('/api/system/password', { password }, {
      category: 'system',
      message: 'Failed to set device password',
      errorRef,
    });
    try {
      const result = await passwordSave;
      if (result.ok) ssh.value = { ...ssh.value, passwordIsDefault: false };
      return result.ok;
    } finally {
      passwordSave = null;
    }
  }

  function handleConflictEvent(event) {
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
    audioCardMissing,
    ssh,
    fetchStatus,
    loadSsh,
    setSshEnabled,
    setPassword,
    recheckHostname,
    handleConflictEvent,
  };
});
