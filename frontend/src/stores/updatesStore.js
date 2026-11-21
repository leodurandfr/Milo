// frontend/src/stores/updatesStore.js
/**
 * Pinia store for program/satellite update state.
 *
 * Owns the program inventories and the in-flight/completed update tracking
 * fed by the `programs/*` WS events (validated and registered centrally in
 * App.vue via parsedOn), so progress survives UpdateManager unmounts and the
 * component only reacts to store state. It also holds where an update of Milō
 * itself stands, which the whole app draws (the update screen, the banners).
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { usePresenceStore } from '@/stores/presenceStore';

// The phases of a Milō update that take the whole screen: the release that is
// leaving installs the new one and reboots into it, and the new one is on
// trial until it has held. After that the arriving release reconciles the
// dependency set and pushes the satellites, behind a banner.
const SCREEN_PHASES = new Set(['download', 'install', 'restart', 'verify']);
const BANNER_PHASES = new Set(['dependencies', 'satellites']);
// A last-update record the user is told about: the new release was put back.
const FAILED_OUTCOMES = new Set(['rolled_back', 'rollback_failed']);
// The banner closed, on the update clock beside the programs' ids but never one
// of them: a close holds back the record a read sent before it carried, and
// nothing else — not where the update stands, not the in-flight set.
const NOTICE_CLOSED = 'milo:notice-closed';

export const useUpdatesStore = defineStore('updates', () => {
  const multiroomStore = useMultiroomStore();
  const presence = usePresenceStore();

  // === STATE ===
  const localPrograms = ref({});
  const localProgramsLoading = ref(true);
  const localProgramsError = ref(false);
  // The server's detail when the last update it was asked to start was
  // refused (a 409 while another runs, a satellite gone): the button goes back,
  // and UpdateManager says why. Feedback on a press, never state: the screen
  // clears it as it opens.
  const startRefusal = ref(null);
  // True from the first loadLocalPrograms() onwards (gates resync, see below)
  const hasEverLoaded = ref(false);

  // The Milō update in progress, {phase, from_version, to_version, item, index,
  // total}, or null: read from GET /api/programs (rebuilt from the update's
  // journal after a reboot) and moved by the `milo` progress events.
  const miloUpdate = ref(null);
  // How the last Milō update ended, {from_version, to_version, outcome, detail,
  // step, dismissed}, or null — kept by the server until the next update
  // begins. `dismissed` is the banner closed on any screen: kept on the server,
  // since the kiosk's private profile forgets what a browser keeps.
  const lastUpdate = ref(null);
  // True once the server has said where a Milō update stands: the boot screen
  // waits for it (bounded), so its first frame after is already the right one.
  const miloUpdateKnown = ref(false);

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
  // The last app push each satellite did not take, by MAC: {verdict, detail}.
  // The server keeps it until that satellite's next success and lists it with
  // the satellite, so a unit that never came back still has a row to say so.
  const satelliteAppFailures = ref({});
  // The last snapclient and CamillaDSP install each satellite did not take, by
  // MAC: {verdict, detail}, from its completion. The server records app pushes
  // alone, so these live here: until that program's next success, or a read
  // of the inventory with nothing more to install on that row.
  const satelliteUpdateFailures = ref({});
  const satelliteCamillaFailures = ref({});
  // The last install of each of this unit's programs that did not take, by
  // key: {error, target, version} — the update's own error line, raw, and what
  // the press asked for, so the row's Retry asks it again. From the completion
  // alone, like the satellites' two above: until that program's next success,
  // or a read of the inventory with nothing left to install on that row.
  // Milō's own outcome is `lastUpdate`, never this.
  const localUpdateFailures = ref({});

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

  // The phase the update screen names, or null when it is not drawn.
  const miloUpdateScreenPhase = computed(() => {
    const phase = miloUpdate.value?.phase;
    return SCREEN_PHASES.has(phase) ? phase : null;
  });

  // The post-commit phase the banner describes, or null. Only while it is
  // installing something: the reads that find nothing to install, the common
  // case, are an update going well, and that shows nothing.
  const miloUpdateBanner = computed(() =>
    BANNER_PHASES.has(miloUpdate.value?.phase) && miloUpdate.value.item ? miloUpdate.value : null);

  // The last update put back, which the Milō row describes until the next one.
  const miloUpdateFailure = computed(() =>
    FAILED_OUTCOMES.has(lastUpdate.value?.outcome) ? lastUpdate.value : null);

  // The same record as a banner, until a screen closed it — and never over an
  // update already running again.
  const rollbackNotice = computed(() => {
    const record = miloUpdateFailure.value;
    if (!record || record.dismissed || miloUpdate.value || isMiloUpdating()) return null;
    return record;
  });

  // === API CALLS ===

  // Mirror the server's in-flight update set into a per-id states ref. In-flight
  // status is delta-only over WS (progress/complete), so a client that loads
  // fresh — reload, second device, backend restarted mid-update, or missed
  // events — has no other way to know an update is running. Adding the server's
  // ids makes the button show "updating" (and lets later progress deltas, which
  // only touch existing entries, attach); clearing ids the server no longer
  // reports recovers from a completion event missed while away.
  // `newer` holds the ids whose state moved after the answer was read: theirs
  // is the more recent word.
  function reconcileActiveUpdates(states, activeIds, newer = null) {
    const active = new Set(activeIds);
    for (const id of active) {
      if (!newer?.has(id) && !states.value[id]?.updating) states.value[id] = { updating: true };
    }
    for (const id of Object.keys(states.value)) {
      if (!newer?.has(id) && !active.has(id)) delete states.value[id];
    }
  }

  // The inventory read probes every program's version, long enough for a
  // press or a WS delta to land while it is in flight. Only the last request
  // sent is applied, and its in-flight set is not mirrored over a program whose
  // update state moved after it left: that state is newer than the answer.
  // The satellite inventory has the same window, once per update kind: each
  // states ref keeps its own count, so a press on one kind leaves the answer in
  // force for the other two.
  function makeDeltaClock() {
    let count = 0;
    const at = new Map(); // id → count at its last move
    return {
      touch: (id) => { at.set(id, ++count); },
      now: () => count,
      movedSince: (seen) => new Set([...at].filter(([, n]) => n > seen).map(([id]) => id))
    };
  }

  let localLoadSeq = 0;
  const localClock = makeDeltaClock();
  const touchLocal = localClock.touch;
  let satLoadSeq = 0;
  const satClock = makeDeltaClock();
  const satAppClock = makeDeltaClock();
  const satCamillaClock = makeDeltaClock();

  function applyMiloUpdate(value) {
    miloUpdate.value = value;
  }

  // Two reads say where a Milō update stands (its own, and the inventory's):
  // the one sent last wins, and neither moves it back past a progress event
  // that landed while it was in flight.
  let miloReadSeq = 0;
  function startMiloRead() {
    return { seq: ++miloReadSeq, seen: localClock.now() };
  }
  // Whenever the answer carries them, the error envelope included: where the
  // Milō update stands does not depend on the version probes that failed. A
  // request that never reached the server says nothing, so it changes nothing —
  // a backend rebooting into the new release keeps its screen.
  // A banner closed while the read was in flight was closed on the record that
  // read carries: it is applied closed.
  function applyMiloRead(read, data) {
    if (read.seq !== miloReadSeq) return;
    const moved = localClock.movedSince(read.seen);
    if (moved.has('milo')) return;
    if (data?.milo_update !== undefined) applyMiloUpdate(data.milo_update);
    if (data?.last_update !== undefined) {
      lastUpdate.value = moved.has(NOTICE_CLOSED) && data.last_update
        ? { ...data.last_update, dismissed: true }
        : data.last_update;
    }
  }

  async function loadMiloUpdate() {
    const read = startMiloRead();
    const result = await apiCall.get('/api/programs/milo/update', {
      category: 'updates',
      message: 'Error reading the Milō update',
      checkStatus: true
    });
    if (result.ok) {
      applyMiloRead(read, result.data);
      miloUpdateKnown.value = true;
    }
    return result.ok;
  }

  // `recheck` is a row's Retry after its check failed: the backend asks GitHub
  // again at once (POST /recheck) instead of answering the failure it
  // remembers, and the rows stay drawn — the pressed button is what waits, not
  // the whole list.
  // A recheck is a row's Retry over a list already drawn: refused, the list
  // stands and the cause goes to `errorRef` — only a read that never got one
  // replaces the screen with the read error.
  async function loadLocalPrograms({ recheck = false, errorRef } = {}) {
    const seq = ++localLoadSeq;
    const deltasSeen = localClock.now();
    const miloRead = startMiloRead();
    hasEverLoaded.value = true;
    if (!recheck) localProgramsLoading.value = true;
    localProgramsError.value = false;
    const options = { category: 'updates', message: 'Error loading programs', checkStatus: true, errorRef };
    const result = recheck
      ? await apiCall.post('/api/programs/recheck', null, options)
      : await apiCall.get('/api/programs', options);
    applyMiloRead(miloRead, result.data);
    if (seq !== localLoadSeq) return result.ok;
    const newer = localClock.movedSince(deltasSeen);
    if (result.ok) {
      localPrograms.value = result.data.programs || {};
      reconcileActiveUpdates(localUpdateStates, result.data.active_updates || [], newer);
      forgetSettledLocalFailures(localPrograms.value, newer);
      // "Completed" means an update finished and this list had not caught up
      // yet — it suppresses one stale frame, nothing more. Kept past the
      // refetch it would outlive its purpose and hide the row's real state:
      // after returning to the manifest, the release upstream is offering again.
      for (const id of [...localCompletedUpdates.value]) {
        if (!newer.has(id)) localCompletedUpdates.value.delete(id);
      }
    } else if (!recheck) {
      localProgramsError.value = true;
    }
    localProgramsLoading.value = false;
    return result.ok;
  }

  // The satellites' "completed" is the same one-frame mark as the local
  // programs', but the read a completion triggers is often taken while the
  // satellite restarts and does not list it: only an answer from that satellite
  // is the frame the mark was bridging.
  function forgetAnsweredCompletions(completed, answered, newer) {
    for (const id of [...completed.value]) {
      if (answered.has(id) && !newer.has(id)) completed.value.delete(id);
    }
  }

  // The server's record is complete: every satellite holding a failure is
  // listed, answering or not, so one missing from the list has none. No delta
  // clock here: every completion that moves a failure sends a read of its own,
  // which supersedes the one in flight.
  function reconcileAppFailures(list) {
    satelliteAppFailures.value = Object.fromEntries(list
      .filter(s => s.app_update_failure)
      .map(s => [s.mac_id, { verdict: s.app_update_failure.verdict, detail: s.app_update_failure.detail ?? null }]));
  }

  // Whether a local program's row offers anything to install: the update
  // button (the manifest's version, or upstream's past it) or the tag menu.
  function offersAnInstall(program) {
    const latest = program?.latest;
    return !!program && !!(program.update_available || latest?.upstream?.ahead
      || latest?.trials?.length || latest?.validated);
  }

  // The local rows' twin of forgetSettledProgramFailures below. `newer` holds
  // the programs whose state moved after the read left: a failure that landed
  // meanwhile is newer than the answer.
  function forgetSettledLocalFailures(programs, newer) {
    for (const id of Object.keys(localUpdateFailures.value)) {
      if (!newer.has(id) && !offersAnInstall(programs[id])) delete localUpdateFailures.value[id];
    }
  }

  // A program failure is about an install still on offer: once a read shows
  // nothing left to install on that row (updated since, from another screen),
  // its failure would describe a state the row no longer is in.
  function forgetSettledProgramFailures(list) {
    for (const sat of list) {
      if (!sat.online) continue;
      if (!sat.update_available) delete satelliteUpdateFailures.value[sat.mac_id];
      if (!sat.camilladsp_update_available) delete satelliteCamillaFailures.value[sat.mac_id];
    }
  }

  async function loadSatellites() {
    // `null` is the never-loaded state, and it is what draws the skeleton —
    // re-entering it on a refresh flashed every satellite section back to
    // skeletons for the length of a fleet-wide probe.
    const seq = ++satLoadSeq;
    const seen = [satClock.now(), satAppClock.now(), satCamillaClock.now()];
    satellitesError.value = false;
    const result = await apiCall.get('/api/programs/satellites', {
      category: 'updates',
      message: 'Error loading satellites',
      checkStatus: true
    });
    if (seq !== satLoadSeq) return result.ok;
    if (result.ok) {
      const list = result.data.satellites || [];
      satellites.value = list;
      const answered = new Set(list.map(s => s.mac_id));
      const newer = [satClock.movedSince(seen[0]), satAppClock.movedSince(seen[1]),
        satCamillaClock.movedSince(seen[2])];
      reconcileActiveUpdates(satelliteUpdateStates, list.filter(s => s.updating).map(s => s.mac_id), newer[0]);
      reconcileActiveUpdates(satelliteAppUpdateStates, list.filter(s => s.app_updating).map(s => s.mac_id), newer[1]);
      reconcileActiveUpdates(satelliteCamillaUpdateStates, list.filter(s => s.camilladsp_updating).map(s => s.mac_id),
        newer[2]);
      forgetAnsweredCompletions(satelliteCompletedUpdates, answered, newer[0]);
      forgetAnsweredCompletions(satelliteAppCompletedUpdates, answered, newer[1]);
      forgetAnsweredCompletions(satelliteCamillaCompletedUpdates, answered, newer[2]);
      reconcileAppFailures(list);
      forgetSettledProgramFailures(list);
    } else {
      satellitesError.value = true;
    }
    return result.ok;
  }

  async function startUpdate(states, id, url, message, body = null, onDelta = null) {
    if (states.value[id]?.updating) return;
    // The target rides along so the button that was pressed can say what it is
    // doing. A client that only learned of the update from the server's
    // in-flight set has no target and falls back to the generic label.
    states.value[id] = { updating: true, target: body?.target, version: body?.version };
    onDelta?.(id);
    startRefusal.value = null;
    // Through the proof of presence: an upstream install reaches root with a
    // release nothing pinned, and the unit waits for a gesture first. Every
    // other start answers as it would straight through apiCall.
    const result = await presence.request('post', url, body, {
      category: 'updates',
      message,
      checkStatus: true,
      errorRef: startRefusal
    });
    if (!result.ok) {
      delete states.value[id];
      onDelta?.(id);
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
  // `version` names one release of the offer's `trials`, and only with 'upstream'.
  async function startLocalUpdate(programKey, target = 'validated', version = null) {
    if (!canUpdateLocal(programKey)) return;
    await startUpdate(localUpdateStates, programKey,
      `/api/programs/${programKey}/update`,
      `Error starting update for ${programKey}`,
      version ? { target, version } : { target },
      touchLocal);
  }

  async function startSatelliteUpdate(macId) {
    await startUpdate(satelliteUpdateStates, macId,
      `/api/programs/satellites/${macId}/update`,
      `Error starting update for satellite ${macId}`, null, satClock.touch);
  }

  async function startSatelliteAppUpdate(macId) {
    await startUpdate(satelliteAppUpdateStates, macId,
      `/api/programs/satellites/${macId}/update-app`,
      `Error starting app update for satellite ${macId}`, null, satAppClock.touch);
  }

  async function startSatelliteCamillaUpdate(macId) {
    await startUpdate(satelliteCamillaUpdateStates, macId,
      `/api/programs/satellites/${macId}/update-camilladsp`,
      `Error starting CamillaDSP update for satellite ${macId}`, null, satCamillaClock.touch);
  }

  // === READ HELPERS ===

  function isLocalUpdating(programKey) {
    return localUpdateStates.value[programKey]?.updating || false;
  }
  function localUpdateTarget(programKey) {
    return localUpdateStates.value[programKey]?.target || null;
  }
  function localUpdateVersion(programKey) {
    return localUpdateStates.value[programKey]?.version || null;
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
  function satelliteAppFailure(macId) {
    return satelliteAppFailures.value[macId] || null;
  }
  function satelliteUpdateFailure(macId) {
    return satelliteUpdateFailures.value[macId] || null;
  }
  function satelliteCamillaFailure(macId) {
    return satelliteCamillaFailures.value[macId] || null;
  }
  function localUpdateFailure(programKey) {
    return localUpdateFailures.value[programKey] || null;
  }

  // Closed at once here, then for every screen by the server, which announces
  // it to the others. A read in flight answers from before the close, so it is
  // not applied over it.
  async function dismissRollbackNotice() {
    if (!rollbackNotice.value) return;
    markLastUpdateDismissed();
    const result = await apiCall.patch('/api/programs/milo/last-update', { dismissed: true }, {
      category: 'updates',
      message: 'Error closing the Milō update notice',
      checkStatus: true
    });
    if (result.ok && result.data?.last_update !== undefined) lastUpdate.value = result.data.last_update;
  }

  // programs/milo_update_notice_dismissed: another screen closed the banner.
  // Noted even over a record this screen has not caught up with: the server's
  // own record is the one closed, and a read in flight is about to bring it.
  function markLastUpdateDismissed() {
    touchLocal(NOTICE_CLOSED);
    if (!lastUpdate.value || lastUpdate.value.dismissed) return;
    lastUpdate.value = { ...lastUpdate.value, dismissed: true };
  }

  // The socket dropped while the update screen was up: the backend is the
  // thing being replaced, so whatever step it last named, it is restarting —
  // until it answers again and says where it stands.
  function handleBackendLost() {
    if (miloUpdateScreenPhase.value) miloUpdate.value = { ...miloUpdate.value, phase: 'restart' };
  }

  // What a running update must block, and no more. The app update blocks
  // everything: it reconciles the whole dependency set, pushes the client app
  // to the fleet and reboots. Local programs block each other because they
  // share dpkg/apt through the deploy wrapper. A satellite is a separate
  // machine — it blocks only its own three buttons, which its API enforces
  // anyway (409 while one runs) — except that two app pushes block each other
  // whatever the satellite: each restarts a speaker, one room cut at a time.
  //
  // The same rule is enforced in `_claim_update` (backend/api/programs.py),
  // which is where it holds: what follows only disables buttons in one
  // client's DOM, and a second device does not see them.
  function isMiloUpdating() {
    return localUpdateStates.value['milo']?.updating || false;
  }

  function isLocalUpdateBusy() {
    return Object.values(localUpdateStates.value).some(state => state.updating);
  }

  function isAnySatelliteUpdating() {
    return [satelliteUpdateStates, satelliteAppUpdateStates, satelliteCamillaUpdateStates]
      .some(states => Object.values(states.value).some(state => state.updating));
  }

  function isSatelliteBusy(macId) {
    return isSatelliteUpdating(macId)
      || isSatelliteAppUpdating(macId)
      || isSatelliteCamillaUpdating(macId);
  }

  function isSatelliteAppBlocked(macId) {
    return isSatelliteBusy(macId)
      || Object.values(satelliteAppUpdateStates.value).some(state => state.updating);
  }

  // === WEBSOCKET HANDLERS ===
  // Payloads validated by parsedOn against @/schemas/ws.js ('programs.*').
  // Progress only updates entries this client knows about (it started them or
  // resynced); completion clears the flag, marks done, and refreshes the list.

  // `onDelta(id)` is told each time a handler moved an entry, for the
  // inventory read in flight to know its answer about that id is older.
  function makeProgressHandler(states, idKey, onDelta = null) {
    return (payload) => {
      const id = payload[idKey];
      if (id && states.value[id]) {
        states.value[id].updating = payload.status === 'updating';
        onDelta?.(id);
      }
    };
  }

  function makeCompleteHandler(states, completed, idKey, reload, onDelta = null) {
    return (payload) => {
      const id = payload[idKey];
      if (!id) return;
      delete states.value[id];
      onDelta?.(id);
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
  //
  // Its API answering is half of the return. The snapclient restarted with it,
  // and Snapcast reports the satellite offline until the reconnection sync
  // admits it again — ~9 s after the completion, measured on both units — and
  // an offline satellite nobody is waiting for has no section at all.
  const SATELLITE_RETURN_ATTEMPTS = 5;
  const SATELLITE_RETURN_DELAY_MS = 4000;

  async function awaitSatelliteReturn(macId) {
    satellitesAwaitingReturn.value.add(macId);
    try {
      for (let attempt = 1; ; attempt++) {
        await loadSatellites();
        const back = satelliteByMacId.value[macId] && multiroomStore.isClientOnline(macId);
        if (back || attempt >= SATELLITE_RETURN_ATTEMPTS) return;
        await new Promise(resolve => setTimeout(resolve, SATELLITE_RETURN_DELAY_MS));
      }
    } finally {
      satellitesAwaitingReturn.value.delete(macId);
    }
  }

  const trackProgramUpdateProgress = makeProgressHandler(localUpdateStates, 'program', touchLocal);
  const completeProgramUpdate = makeCompleteHandler(localUpdateStates, localCompletedUpdates, 'program', loadLocalPrograms, touchLocal);
  // A `milo` progress event naming a phase says where the whole update stands,
  // so it replaces the record rather than patching it.
  function handleProgramUpdateProgress(payload) {
    if (payload.program === 'milo' && payload.phase) {
      applyMiloUpdate({
        phase: payload.phase,
        from_version: payload.from_version ?? null,
        to_version: payload.to_version ?? null,
        item: payload.item ?? null,
        index: payload.index ?? null,
        total: payload.total ?? null,
      });
      touchLocal('milo');
    }
    trackProgramUpdateProgress(payload);
  }
  // Sent once the arriving release has finished its satellites, or when the
  // leaving one refused before anything moved. Never before the reboot.
  // Any other program's failure stays on its row, read before the completion
  // drops what the press asked for.
  function handleProgramUpdateComplete(payload) {
    if (payload.program === 'milo') {
      applyMiloUpdate(null);
      touchLocal('milo');
    } else if (payload.program && payload.success) {
      delete localUpdateFailures.value[payload.program];
    } else if (payload.program) {
      const asked = localUpdateStates.value[payload.program];
      localUpdateFailures.value[payload.program] = {
        error: payload.error ?? null,
        target: asked?.target ?? null,
        version: asked?.version ?? null,
      };
    }
    completeProgramUpdate(payload);
  }
  const handleSatelliteUpdateProgress = makeProgressHandler(satelliteUpdateStates, 'mac_id', satClock.touch);
  // A snapclient or CamillaDSP install says how it ended too: its failure stays
  // on the row, the satellite's own error line under it.
  function keepingFailure(failures, complete) {
    return (payload) => {
      const id = payload.mac_id;
      if (!id) return;
      if (payload.success) {
        delete failures.value[id];
      } else {
        failures.value[id] = { verdict: payload.verdict ?? null, detail: payload.detail ?? null };
      }
      complete(payload);
    };
  }
  const handleSatelliteUpdateComplete = keepingFailure(satelliteUpdateFailures,
    makeCompleteHandler(satelliteUpdateStates, satelliteCompletedUpdates, 'mac_id', awaitSatelliteReturn, satClock.touch));
  const handleSatelliteAppUpdateProgress = makeProgressHandler(satelliteAppUpdateStates, 'mac_id', satAppClock.touch);
  const completeSatelliteAppUpdate = makeCompleteHandler(satelliteAppUpdateStates, satelliteAppCompletedUpdates, 'mac_id', awaitSatelliteReturn, satAppClock.touch);
  // A failed app push is not the end of the row: a satellite that went back to
  // its previous version restarted too, and one that did not return is still
  // listed by the server with its failure — both are read again.
  function handleSatelliteAppUpdateComplete(payload) {
    const id = payload.mac_id;
    if (!id) return;
    if (payload.success) {
      delete satelliteAppFailures.value[id];
    } else {
      satelliteAppFailures.value[id] = { verdict: payload.verdict ?? null, detail: payload.detail ?? null };
    }
    completeSatelliteAppUpdate(payload);
    if (!payload.success) awaitSatelliteReturn(id);
  }
  const handleSatelliteCamillaUpdateProgress = makeProgressHandler(satelliteCamillaUpdateStates, 'mac_id', satCamillaClock.touch);
  const handleSatelliteCamillaUpdateComplete = keepingFailure(satelliteCamillaFailures,
    makeCompleteHandler(satelliteCamillaUpdateStates, satelliteCamillaCompletedUpdates, 'mac_id', awaitSatelliteReturn, satCamillaClock.touch));

  // Reconciles in-flight update flags so "updating" survives a reconnect/foreground.
  // Where a Milō update stands is read on every resync, the boot's included:
  // every screen draws it — a kiosk coming back from the update's reboot, a
  // phone opened halfway through one — and its read costs nothing. The program
  // list costs an installed-version probe per program (and a GitHub read), so
  // it is only refetched once a view has loaded it.
  //
  // Satellites carry the same delta-only in-flight flags and need the same heal,
  // but they only exist while multiroom is on. `satellites !== null` is that gate:
  // it means UpdateManager already fetched them, which it only does when multiroom
  // is enabled — no second copy of the condition to keep in step.
  async function resync() {
    const tasks = [loadMiloUpdate()];
    if (hasEverLoaded.value) tasks.push(loadLocalPrograms());
    if (satellites.value !== null) tasks.push(loadSatellites());
    return (await Promise.all(tasks)).every(Boolean);
  }

  return {
    resync,
    // State
    localPrograms,
    localProgramsLoading,
    localProgramsError,
    startRefusal,
    satellites,
    satellitesError,
    miloUpdate,
    lastUpdate,
    miloUpdateKnown,

    // Computed
    satelliteByMacId,
    miloUpdateScreenPhase,
    miloUpdateBanner,
    miloUpdateFailure,
    rollbackNotice,

    // Actions
    loadLocalPrograms,
    loadMiloUpdate,
    loadSatellites,
    canUpdateLocal,
    startLocalUpdate,
    startSatelliteUpdate,
    startSatelliteAppUpdate,
    startSatelliteCamillaUpdate,
    dismissRollbackNotice,
    markLastUpdateDismissed,
    handleBackendLost,

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
    satelliteAppFailure,
    satelliteUpdateFailure,
    satelliteCamillaFailure,
    localUpdateFailure,
    localUpdateTarget,
    localUpdateVersion,
    isMiloUpdating,
    isLocalUpdateBusy,
    isAnySatelliteUpdating,
    isSatelliteBusy,
    isSatelliteAppBlocked,

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
