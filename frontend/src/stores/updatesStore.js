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

export const useUpdatesStore = defineStore('updates', () => {
  // === STATE ===
  const localPrograms = ref({});
  const localProgramsLoading = ref(true);
  const localProgramsError = ref(false);
  // True from the first loadLocalPrograms() onwards (gates resync, see below)
  const hasEverLoaded = ref(false);

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
  // Satellites whose own API we are waiting to answer again after they
  // restarted themselves. Kept apart from the update states above because
  // reconcileActiveUpdates clears those from the server's in-flight set, which
  // no longer holds an update the server considers finished.
  const satellitesAwaitingReturn = ref(new Set());

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
    hasEverLoaded.value = true;
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
    // `null` is the never-loaded state, and it is what draws the skeleton —
    // re-entering it on a refresh flashed every satellite section back to
    // skeletons for the length of a fleet-wide probe.
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

  async function startUpdate(states, id, url, message, body = null) {
    if (states.value[id]?.updating) return;
    states.value[id] = { updating: true };
    const result = await apiCall.post(url, body, {
      category: 'updates',
      message,
      checkStatus: true
    });
    if (!result.ok) {
      delete states.value[id];
    }
  }

  // The updatable programs are exactly the ones GET /api/programs returned:
  // the backend builds that response from its own catalog. Restating the list
  // here made adding a program a two-repo edit, and forgetting this half hid
  // the new program's update button with no error.
  function canUpdateLocal(programKey) {
    return programKey in localPrograms.value;
  }

  // `target` names the release to install: 'validated' is the version
  // dependencies.env declares — and, on a unit deliberately moved past it, the
  // return to that version — while 'upstream' is what GitHub published beyond
  // the manifest. The backend decides which are on offer; this only forwards it.
  async function startLocalUpdate(programKey, target = 'validated') {
    if (!canUpdateLocal(programKey)) return;
    await startUpdate(localUpdateStates, programKey,
      `/api/programs/${programKey}/update`,
      `Error starting update for ${programKey}`,
      { target });
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
  function isSatelliteAwaitingReturn(macId) {
    return satellitesAwaitingReturn.value.has(macId);
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
        reload(id);
      }
    };
  }

  // A satellite restarts its own services as it reports an update finished, so
  // the inventory that completion triggers can be read while its API is still
  // down: the server then answers without it — measured once 8 s after the
  // press, one satellite of two in the payload — and nothing else ever fetched
  // it again, leaving its row a skeleton until the page was reloaded. Poll
  // until it answers, bounded: what outlives the bound is a satellite that
  // really is not answering, and the row says so in words.
  const SATELLITE_RETURN_ATTEMPTS = 5;
  const SATELLITE_RETURN_DELAY_MS = 4000;

  async function awaitSatelliteReturn(macId) {
    satellitesAwaitingReturn.value.add(macId);
    try {
      for (let attempt = 1; ; attempt++) {
        await loadSatellites();
        if (satelliteByMacId.value[macId] || attempt >= SATELLITE_RETURN_ATTEMPTS) return;
        await new Promise(resolve => setTimeout(resolve, SATELLITE_RETURN_DELAY_MS));
      }
    } finally {
      satellitesAwaitingReturn.value.delete(macId);
    }
  }

  const handleProgramUpdateProgress = makeProgressHandler(localUpdateStates, 'program');
  const handleProgramUpdateComplete = makeCompleteHandler(localUpdateStates, localCompletedUpdates, 'program', loadLocalPrograms);
  const handleSatelliteUpdateProgress = makeProgressHandler(satelliteUpdateStates, 'mac_id');
  const handleSatelliteUpdateComplete = makeCompleteHandler(satelliteUpdateStates, satelliteCompletedUpdates, 'mac_id', awaitSatelliteReturn);
  const handleSatelliteAppUpdateProgress = makeProgressHandler(satelliteAppUpdateStates, 'mac_id');
  const handleSatelliteAppUpdateComplete = makeCompleteHandler(satelliteAppUpdateStates, satelliteAppCompletedUpdates, 'mac_id', awaitSatelliteReturn);
  const handleSatelliteCamillaUpdateProgress = makeProgressHandler(satelliteCamillaUpdateStates, 'mac_id');
  const handleSatelliteCamillaUpdateComplete = makeCompleteHandler(satelliteCamillaUpdateStates, satelliteCamillaCompletedUpdates, 'mac_id', awaitSatelliteReturn);

  // Reconciles in-flight update flags so "updating" survives a reconnect/foreground.
  // Lazily loaded: UpdateManager calls loadLocalPrograms() when it opens, and the
  // call costs an installed-version probe per program — same gate as radioStore.
  //
  // Satellites carry the same delta-only in-flight flags and need the same heal,
  // but they only exist while multiroom is on. `satellites !== null` is that gate:
  // it means UpdateManager already fetched them, which it only does when multiroom
  // is enabled — no second copy of the condition to keep in step.
  async function resync() {
    if (!hasEverLoaded.value) return;
    const tasks = [loadLocalPrograms()];
    if (satellites.value !== null) tasks.push(loadSatellites());
    return Promise.all(tasks);
  }

  return {
    resync,
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
    isSatelliteAwaitingReturn,
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
