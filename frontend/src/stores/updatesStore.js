// frontend/src/stores/updatesStore.js
/**
 * Pinia store for program/satellite update state.
 *
 * Owns the program inventories and the in-flight/completed update tracking
 * fed by the `programs/*` WS events (validated and registered centrally in
 * App.vue via parsedOn), so progress survives UpdateManager unmounts and the
 * component only reacts to store state.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';

const SUPPORTED_LOCAL_UPDATES = ['milo', 'go-librespot', 'shairport-sync', 'multiroom', 'camilladsp'];

export const useUpdatesStore = defineStore('updates', () => {
  // === STATE ===
  const localPrograms = ref({});
  const localProgramsLoading = ref(true);
  const localProgramsError = ref(false);

  const satellites = ref(null); // null = not loaded, [] = loaded empty
  const satellitesError = ref(false);

  // In-flight flags + completed markers, keyed by program key or satellite MAC
  const localUpdateStates = ref({});
  const localCompletedUpdates = ref(new Set());
  const satelliteUpdateStates = ref({});
  const satelliteCompletedUpdates = ref(new Set());
  const satelliteAppUpdateStates = ref({});
  const satelliteAppCompletedUpdates = ref(new Set());
  const satelliteCamillaUpdateStates = ref({});
  const satelliteCamillaCompletedUpdates = ref(new Set());

  // === COMPUTED ===

  // Lookup map: mac_id → satellite data
  const satelliteByMacId = computed(() => {
    if (!satellites.value) return {};
    const map = {};
    for (const sat of satellites.value) {
      map[sat.mac_id] = sat;
    }
    return map;
  });

  // === API CALLS ===

  // Mirror the server's in-flight update set into a per-id states ref. In-flight
  // status is delta-only over WS (progress/complete), so a client that loads
  // fresh — reload, second device, backend restarted mid-update, or missed
  // events — has no other way to know an update is running. Adding the server's
  // ids makes the button show "updating" (and lets later progress deltas, which
  // only touch existing entries, attach); clearing ids the server no longer
  // reports recovers from a completion event missed while away.
  function reconcileActiveUpdates(states, activeIds) {
    const active = new Set(activeIds);
    for (const id of active) {
      if (!states.value[id]?.updating) states.value[id] = { updating: true };
    }
    for (const id of Object.keys(states.value)) {
      if (!active.has(id)) delete states.value[id];
    }
  }

  async function loadLocalPrograms() {
    localProgramsLoading.value = true;
    localProgramsError.value = false;
    const result = await apiCall.get('/api/programs', {
      category: 'updates',
      message: 'Error loading programs',
      checkStatus: true
    });
    if (result.ok) {
      localPrograms.value = result.data.programs || {};
      reconcileActiveUpdates(localUpdateStates, result.data.active_updates || []);
    } else {
      localProgramsError.value = true;
    }
    localProgramsLoading.value = false;
  }

  async function loadSatellites() {
    satellites.value = null;
    satellitesError.value = false;
    const result = await apiCall.get('/api/programs/satellites', {
      category: 'updates',
      message: 'Error loading satellites',
      checkStatus: true
    });
    if (result.ok) {
      const list = result.data.satellites || [];
      satellites.value = list;
      reconcileActiveUpdates(satelliteUpdateStates, list.filter(s => s.updating).map(s => s.mac_id));
      reconcileActiveUpdates(satelliteAppUpdateStates, list.filter(s => s.app_updating).map(s => s.mac_id));
      reconcileActiveUpdates(satelliteCamillaUpdateStates, list.filter(s => s.camilladsp_updating).map(s => s.mac_id));
    } else {
      satellitesError.value = true;
    }
  }

  async function startUpdate(states, id, url, message) {
    if (states.value[id]?.updating) return;
    states.value[id] = { updating: true };
    const result = await apiCall.post(url, null, {
      category: 'updates',
      message,
      checkStatus: true
    });
    if (!result.ok) {
      delete states.value[id];
    }
  }

  function canUpdateLocal(programKey) {
    return SUPPORTED_LOCAL_UPDATES.includes(programKey);
  }

  async function startLocalUpdate(programKey) {
    if (!canUpdateLocal(programKey)) return;
    await startUpdate(localUpdateStates, programKey,
      `/api/programs/${programKey}/update`,
      `Error starting update for ${programKey}`);
  }

  async function startSatelliteUpdate(macId) {
    await startUpdate(satelliteUpdateStates, macId,
      `/api/programs/satellites/${macId}/update`,
      `Error starting update for satellite ${macId}`);
  }

  async function startSatelliteAppUpdate(macId) {
    await startUpdate(satelliteAppUpdateStates, macId,
      `/api/programs/satellites/${macId}/update-app`,
      `Error starting app update for satellite ${macId}`);
  }

  async function startSatelliteCamillaUpdate(macId) {
    await startUpdate(satelliteCamillaUpdateStates, macId,
      `/api/programs/satellites/${macId}/update-camilladsp`,
      `Error starting CamillaDSP update for satellite ${macId}`);
  }

  // === READ HELPERS ===

  function isLocalUpdating(programKey) {
    return localUpdateStates.value[programKey]?.updating || false;
  }
  function isLocalUpdateCompleted(programKey) {
    return localCompletedUpdates.value.has(programKey);
  }
  function isSatelliteUpdating(macId) {
    return satelliteUpdateStates.value[macId]?.updating || false;
  }
  function isSatelliteUpdateCompleted(macId) {
    return satelliteCompletedUpdates.value.has(macId);
  }
  function isSatelliteAppUpdating(macId) {
    return satelliteAppUpdateStates.value[macId]?.updating || false;
  }
  function isSatelliteAppUpdateCompleted(macId) {
    return satelliteAppCompletedUpdates.value.has(macId);
  }
  function isSatelliteCamillaUpdating(macId) {
    return satelliteCamillaUpdateStates.value[macId]?.updating || false;
  }
  function isSatelliteCamillaUpdateCompleted(macId) {
    return satelliteCamillaCompletedUpdates.value.has(macId);
  }

  function isAnyUpdateInProgress() {
    return [localUpdateStates, satelliteUpdateStates, satelliteAppUpdateStates, satelliteCamillaUpdateStates]
      .some(states => Object.values(states.value).some(state => state.updating));
  }

  // === WEBSOCKET HANDLERS ===
  // Payloads validated by parsedOn against @/schemas/ws.js ('programs.*').
  // Progress only updates entries this client knows about (it started them or
  // resynced); completion clears the flag, marks done, and refreshes the list.

  function makeProgressHandler(states, idKey) {
    return (payload) => {
      const id = payload[idKey];
      if (id && states.value[id]) {
        states.value[id].updating = payload.status === 'updating';
      }
    };
  }

  function makeCompleteHandler(states, completed, idKey, reload) {
    return (payload) => {
      const id = payload[idKey];
      if (!id) return;
      delete states.value[id];
      if (payload.success) {
        completed.value.add(id);
        reload();
      }
    };
  }

  const handleProgramUpdateProgress = makeProgressHandler(localUpdateStates, 'program');
  const handleProgramUpdateComplete = makeCompleteHandler(localUpdateStates, localCompletedUpdates, 'program', loadLocalPrograms);
  const handleSatelliteUpdateProgress = makeProgressHandler(satelliteUpdateStates, 'mac_id');
  const handleSatelliteUpdateComplete = makeCompleteHandler(satelliteUpdateStates, satelliteCompletedUpdates, 'mac_id', loadSatellites);
  const handleSatelliteAppUpdateProgress = makeProgressHandler(satelliteAppUpdateStates, 'mac_id');
  const handleSatelliteAppUpdateComplete = makeCompleteHandler(satelliteAppUpdateStates, satelliteAppCompletedUpdates, 'mac_id', loadSatellites);
  const handleSatelliteCamillaUpdateProgress = makeProgressHandler(satelliteCamillaUpdateStates, 'mac_id');
  const handleSatelliteCamillaUpdateComplete = makeCompleteHandler(satelliteCamillaUpdateStates, satelliteCamillaCompletedUpdates, 'mac_id', loadSatellites);

  return {
    // State
    localPrograms,
    localProgramsLoading,
    localProgramsError,
    satellites,
    satellitesError,

    // Computed
    satelliteByMacId,

    // Actions
    loadLocalPrograms,
    loadSatellites,
    canUpdateLocal,
    startLocalUpdate,
    startSatelliteUpdate,
    startSatelliteAppUpdate,
    startSatelliteCamillaUpdate,

    // Read helpers
    isLocalUpdating,
    isLocalUpdateCompleted,
    isSatelliteUpdating,
    isSatelliteUpdateCompleted,
    isSatelliteAppUpdating,
    isSatelliteAppUpdateCompleted,
    isSatelliteCamillaUpdating,
    isSatelliteCamillaUpdateCompleted,
    isAnyUpdateInProgress,

    // WebSocket handlers
    handleProgramUpdateProgress,
    handleProgramUpdateComplete,
    handleSatelliteUpdateProgress,
    handleSatelliteUpdateComplete,
    handleSatelliteAppUpdateProgress,
    handleSatelliteAppUpdateComplete,
    handleSatelliteCamillaUpdateProgress,
    handleSatelliteCamillaUpdateComplete
  };
});
