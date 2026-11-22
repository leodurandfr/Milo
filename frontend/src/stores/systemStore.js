// frontend/src/stores/systemStore.js
/**
 * Pinia store for system-level state.
 *
 * Tracks whether another Milō server has claimed `milo.local` on the local
 * network (mDNS hostname conflict; the backend re-checks every 5 minutes and
 * broadcasts state changes via WebSocket), the audio card hardware.json names
 * when ALSA cannot see it, the files the backend could not read and set
 * aside, and the SSH / account password panel of Réglages › Système.
 */
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { apiCall } from '@/services/apiCall';

export const useSystemStore = defineStore('system', () => {
  const hostnameConflict = ref(false);
  const advertisedName = ref(null);
  const localIp = ref(null);
  const rechecking = ref(false);
  // The label of the audio card hardware.json names when ALSA cannot see it,
  // null when all is well. A HAT is not hot-pluggable, so this is settled at
  // boot and arrives with the status read rather than as a WS delta.
  const reportedAudioCardMissing = ref(null);
  // The status read names the card on every resync, so a dismissal is kept as
  // the label dismissed, until a read reports anything else (another card,
  // or none). Per tab, never stored — the backend wants the banner back on
  // every load.
  const dismissedAudioCardMissing = ref(null);
  // The wizard's choices are applied and the unit has not joined a network
  // since: the wizard opens on its Network step (backend/api/setup.py). Read
  // with the status, on every load.
  const setupAwaitingNetwork = ref(false);
  // The files the backend could not read and set aside, each started again
  // from its defaults (backend/core/system/notices.py). Kept there until
  // acknowledged, so they come with the initial state, `system/notices_changed`
  // and every resync — a boot's set-asides happen before any page listens.
  const notices = ref([]);
  // The ids this page has closed. Each acknowledgement is announced with the
  // list still left, so with two or more in flight the first announcement
  // names the others: kept out until their own DELETE says otherwise, or the
  // banner just closed comes back for a moment. Ids are never reused.
  const acknowledged = new Set();
  const audioCardMissing = computed(() => (
    reportedAudioCardMissing.value === dismissedAudioCardMissing.value
      ? null
      : reportedAudioCardMissing.value
  ));

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
    if (typeof state.setup_awaiting_network === 'boolean') {
      setupAwaitingNetwork.value = state.setup_awaiting_network;
    }
    if (state.audio_card_missing !== undefined) {
      reportedAudioCardMissing.value = state.audio_card_missing;
      if (state.audio_card_missing !== dismissedAudioCardMissing.value) {
        dismissedAudioCardMissing.value = null;
      }
    }
  }

  function applyNotices(list) {
    if (Array.isArray(list)) notices.value = list.filter((notice) => !acknowledged.has(notice.id));
  }

  function handleNoticesEvent(event) {
    applyNotices(event?.data?.notices);
  }

  async function fetchNotices() {
    const result = await apiCall.get('/api/system/notices', {
      category: 'system',
      message: 'Error fetching the set-aside notices',
      checkStatus: true,
    });
    if (result.ok) applyNotices(result.data.data.notices);
    return result.ok;
  }

  // The banner closed: every notice it named is read, on every screen. Hidden
  // at once. A 404 is another screen having acknowledged it first; any other
  // refusal (a card that takes no write) puts it back, as the backend kept it.
  async function acknowledgeNotices() {
    const ids = notices.value.map((notice) => notice.id);
    ids.forEach((id) => acknowledged.add(id));
    notices.value = [];
    const results = await Promise.all(ids.map((id) => apiCall.delete(
      `/api/system/notices/${encodeURIComponent(id)}`,
      { category: 'system', message: 'Error acknowledging a set-aside notice' },
    )));
    const refused = ids.filter((_, i) => !results[i].ok && results[i].error?.status !== 404);
    if (refused.length) {
      refused.forEach((id) => acknowledged.delete(id));
      await fetchNotices();
    }
  }

  function dismissAudioCardMissing() {
    dismissedAudioCardMissing.value = reportedAudioCardMissing.value;
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
    const [status, kept] = await Promise.all([fetchStatus(), fetchNotices()]);
    return status && kept;
  }

  return {
    resync,
    hostnameConflict,
    advertisedName,
    localIp,
    rechecking,
    audioCardMissing,
    dismissAudioCardMissing,
    setupAwaitingNetwork,
    notices,
    applyNotices,
    handleNoticesEvent,
    acknowledgeNotices,
    ssh,
    fetchStatus,
    loadSsh,
    setSshEnabled,
    setPassword,
    recheckHostname,
    handleConflictEvent,
  };
});
