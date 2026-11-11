// frontend/src/stores/unifiedAudioStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { reportError } from '@/services/errorReporter';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { AudioStateSchema, VolumeStateSchema, validateSchema } from '@/schemas/api';
import { DEFAULT_VOLUME_DB, MAX_ADJUST_DB } from '@/constants/volume';
import { isKiosk } from '@/utils/kiosk';

// How long a radio player shows a station just tuned alone, before the song it
// plays (usePlayerMetadata).
export const STATION_INTRO_MS = 2500;

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === THE AUDIO STATE ===
  // The backend's AudioState, whole (docs: "Développeurs : le fil"): the
  // selection, the service, every source's availability, the session with its
  // position anchor, the commands the source takes now, the resume point and
  // the source's own content. Replaced by every `source/state`, never merged;
  // only `source/position` moves the anchor between two of them.
  const systemState = ref({
    source: 'none',
    switching: false,
    service: 'stopped',
    service_error: null,
    availability: {},
    session: null,
    controls: [],
    resume: null,
    details: null,
    multiroom_enabled: false,
    equalizer_effects_enabled: false,
  });

  // The session is loading and has never been past `loading`: its first wait,
  // which is a real one (a disc read, a file opened), unlike the loading a
  // track change passes through.
  const sessionOpening = ref(false);
  let settledSessionId = null;

  // The radio session a live `source/state` opened on another station, for
  // its first STATION_INTRO_MS; null otherwise. One clock for every player, so
  // they all end the intro on the same tick. A session first seen in a
  // snapshot (initial_state, resync) opened before this page looked, and gets
  // none.
  const introSessionId = ref(null);
  let seenSessionId = null;
  let seenStationId = null;
  let introTimer = null;

  // === VOLUME STATE (unified structure) ===
  const volumeState = ref({
    mode: 'direct',                  // 'direct' or 'multiroom'
    global_volume_db: DEFAULT_VOLUME_DB, // Global volume (average of the playing clients)
    global_volume: 0,                // The same, 0..1 over the limits — computed by the server
    global_mute: false,              // Global mute state
    volume_control: true,            // False = DAC mode (external amp manages volume)
    any_volume_control: true,        // True if any device manages volume via Milo
    clients: {},                     // {mac: {volume_db, mute, available}} — what VolumeClientSchema keeps
    zones: {},                       // {zoneId: {id, name, client_ids, average_volume_db, all_muted}}
  });

  // Volume bar visibility state (replaces component coupling)
  const showVolumeBar = ref(false);
  let volumeBarHideTimer = null;

  // How long a change stays on screen after the last one: the bar's, and the
  // panel's when a remote control opened it.
  const VOLUME_SHOWN_MS = 3000;

  // Transient command error (set on sendCommand failure, consumed by App.vue)
  const commandError = ref(null);

  // Generic transient notice ({ title, detail }) surfaced in the global banner.
  // Set by any feature, consumed + auto-dismissed by App.vue.
  const transientNotice = ref(null);


  // === AUDIO ACTIONS ===
  async function changeSource(source) {
    const result = await apiCall.post(`/api/audio/source/${source}`, null, {
      category: 'store',
      message: `Change source failed: ${source}`,
      checkStatus: true,
    });
    return result.ok;
  }

  async function sendCommand(source, command, data = {}) {
    const result = await apiCall.post(`/api/audio/control/${source}`, { command, data }, {
      category: 'store',
      message: `Command failed: ${source}/${command}`,
      checkStatus: true,
    });
    if (!result.ok) {
      commandError.value = { source, command };
    }
    return result.ok;
  }

  // The mode this tab asked for, while the request carrying it is in flight.
  // The PUT answers only once the whole switch is done — seconds — and the state
  // carries the new mode only then, so a switch drawn from the state alone would
  // sit on the old mode for the whole wait. Dropped when the request settles:
  // done, the state already says so; refused, the old mode is the truth.
  const requestedMultiroom = ref(null);
  let multiroomRequestSeq = 0;

  /** The multiroom switch's position: the request in flight, else the state. */
  const multiroomTarget = computed(() =>
    requestedMultiroom.value ?? systemState.value.multiroom_enabled
  );

  async function setMultiroomEnabled(enabled) {
    const seq = ++multiroomRequestSeq;
    requestedMultiroom.value = enabled;
    try {
      const result = await apiCall.put('/api/routing/multiroom', { enabled }, {
        category: 'store',
        message: 'Set multiroom failed',
        checkStatus: true,
      });
      return result.ok;
    } finally {
      if (seq === multiroomRequestSeq) requestedMultiroom.value = null;
    }
  }

  // === DISCONNECT ===
  const disconnectingStates = ref({});

  async function disconnectSource(source) {
    if (!source || source === 'none') return false;
    disconnectingStates.value[source] = true;

    let success = false;
    if (source === 'bluetooth') {
      // Disconnect flows through the generic control endpoint (the dedicated
      // /api/bluetooth router was retired). Two callers: the status card's CTA
      // and BluetoothSource's action button, since the card gives way to the
      // player as soon as the sender publishes a track.
      success = await sendCommand('bluetooth', 'disconnect');
    } else {
      logger.warn('store', `Disconnect not supported for ${source}`);
    }

    setTimeout(() => {
      disconnectingStates.value[source] = false;
    }, 900);

    return success;
  }

  function isDisconnecting(source) {
    return disconnectingStates.value[source] || false;
  }

  // === VOLUME ACTIONS (all in dB) ===
  // One /adjust in flight at a time. A held dock button asks every 50 ms,
  // faster than a satellite can answer: sent as they came, the requests piled
  // up and the level went on climbing after the release. What arrives while
  // one is in flight is summed and sent as one when it returns, so a release
  // leaves at most one request behind it.
  let adjustInFlight = null;
  let pendingDelta = 0;

  function adjustVolume(delta_db) {
    pendingDelta += delta_db;
    // Started only with something to send, so the drain always reaches its
    // first request before it can finish — and it is assigned before that.
    if (!adjustInFlight && pendingDelta !== 0) adjustInFlight = drainAdjust();
    return adjustInFlight ?? Promise.resolve(true);
  }

  /** Sends until nothing is pending; true iff every request succeeded. */
  async function drainAdjust() {
    let ok = true;
    while (pendingDelta !== 0) {
      // A level the Volume panel's slider still has queued was asked first; a
      // step sent past it would be overwritten by it. Only this side waits, so
      // the two queues can never wait on each other.
      while (globalInFlight) await globalInFlight;
      // The route's own bound on one delta: a sum gathered behind a slow
      // request can exceed it, and leaves in several.
      const delta_db = Math.max(-MAX_ADJUST_DB, Math.min(MAX_ADJUST_DB, pendingDelta));
      pendingDelta -= delta_db;
      const result = await apiCall.post('/api/volume/adjust', { delta_db, show_bar: true }, {
        category: 'store',
        message: 'Adjust volume failed',
        checkStatus: true,
      });
      ok = ok && result.ok;
    }
    // Cleared in the same step as the check above, so a press arriving after
    // it starts a new drain rather than joining one that has ended.
    adjustInFlight = null;
    return ok;
  }

  // An absolute level (0..1 over the limits the server runs on), for the Volume
  // panel's slider. Same shape as /adjust above, latest value wins: a drag asks
  // far faster than a satellite answers, and two levels in flight could land in
  // either order, leaving the house at the older one — with nothing on screen
  // to say so, since the echo of the newer one would already have passed.
  let globalInFlight = null;
  let pendingGlobal = null;

  function setGlobalVolume(level) {
    pendingGlobal = Math.max(0, Math.min(1, level));
    if (!globalInFlight) globalInFlight = drainGlobal();
    return globalInFlight;
  }

  /** Sends until nothing is pending; true iff the last request succeeded. */
  async function drainGlobal() {
    let ok = true;
    while (pendingGlobal !== null) {
      const volume = pendingGlobal;
      pendingGlobal = null;
      // show_bar stays on: the other screens show the bar as they do for any
      // change, and the screen holding the panel hides its own (VolumeBar).
      const result = await apiCall.patch('/api/volume/global', { volume }, {
        category: 'store',
        message: 'Set volume failed',
        checkStatus: true,
      });
      ok = result.ok;
    }
    globalInFlight = null;
    return ok;
  }

  // Open on this screen only — never on the wire. The bar (applyVolumeState) and
  // the kiosk's auto-player read it: the panel already shows the level, and
  // opening the full player behind it would change the view it closes onto.
  const volumePanelOpen = ref(false);

  // The same panel opened by a remote control (`display: "panel"`) rather than
  // from the dock: shown, not held — no − / +, nothing to drag, gone
  // VOLUME_SHOWN_MS after the last press, as the bar is.
  const volumePanelPassive = ref(false);

  // The panel's one countdown, whichever opened it, re-armed by any use of it —
  // a remote's press included. The held panel's is short next to a modal's
  // 120 s: nothing in it is read for long.
  const HELD_PANEL_CLOSE_MS = 30000;
  let panelCloseTimer = null;

  function clearPanelClose() {
    if (panelCloseTimer) clearTimeout(panelCloseTimer);
    panelCloseTimer = null;
  }

  /** Starts the panel's countdown over; a no-op while it is closed. */
  function armVolumePanelClose() {
    clearPanelClose();
    if (!volumePanelOpen.value) return;
    panelCloseTimer = setTimeout(closeVolumePanel,
      volumePanelPassive.value ? VOLUME_SHOWN_MS : HELD_PANEL_CLOSE_MS);
  }

  /** A finger resting on the track is still in use, moving or not: no
   *  countdown runs until armVolumePanelClose() is called again. */
  function holdVolumePanel() {
    clearPanelClose();
  }

  function openVolumePanel() {
    hideVolumeBar();
    volumePanelPassive.value = false;
    volumePanelOpen.value = true;
    armVolumePanelClose();
  }

  function showPassivePanel() {
    hideVolumeBar();
    volumePanelPassive.value = true;
    volumePanelOpen.value = true;
    armVolumePanelClose();
  }

  // Held while the panel's last requests drain after it closed: their echoes
  // would otherwise bring the bar up over the screen the panel closed onto.
  let barQuietAfterPanel = false;

  function closeVolumePanel() {
    clearPanelClose();
    volumePanelPassive.value = false;
    volumePanelOpen.value = false;
    hideVolumeBar();
    const draining = [globalInFlight, adjustInFlight].filter(Boolean);
    if (!draining.length) return;
    barQuietAfterPanel = true;
    Promise.allSettled(draining).then(() => { barQuietAfterPanel = false; });
  }

  // === STATE UPDATE ===
  // Strict: a state that does not parse is refused whole and the last good one
  // stays — never half-applied with defaults. The first refusal of the page is
  // reported to /api/errors (errors.log): the kiosk's console reaches no log.
  let refusalReported = false;
  function updateSystemState(newState, origin = 'unknown', live = false) {
    const result = validateSchema(AudioStateSchema, newState, `AudioState from ${origin}`);
    if (!result.success) {
      logger.warn('store', `Audio state from ${origin} refused: it does not match the wire`,
        result.error?.issues);
      if (!refusalReported) {
        refusalReported = reportError('audio-state', `AudioState from ${origin} refused by AudioStateSchema`,
          JSON.stringify(result.error?.issues));
      }
      return false;
    }
    systemState.value = result.data;
    const session = result.data.session;
    if (session && session.phase !== 'loading') settledSessionId = session.id;
    sessionOpening.value = session?.phase === 'loading' && session.id !== settledSessionId;
    // Keyed on the station, not the session alone: a multiroom toggle re-tunes
    // the same station under a new session id, and the song must stay put.
    const station = session && result.data.source === 'radio' ? result.data.details?.station : null;
    if (session && session.id !== seenSessionId) {
      seenSessionId = session.id;
      if (live && station?.name && station.id !== seenStationId) startIntro(session.id);
    }
    if (station) seenStationId = station.id;
    return true;
  }

  function startIntro(sessionId) {
    clearTimeout(introTimer);
    introSessionId.value = sessionId;
    introTimer = setTimeout(() => { introSessionId.value = null; }, STATION_INTRO_MS);
  }

  // Counted so resync() can tell a snapshot read before a delta it just
  // received. Plain lets, not refs: nothing renders them.
  let audioDeltas = 0;
  let volumeDeltas = 0;
  let lastPosition = null;

  /** `source/state` (its data IS the state) and `system/initial_state` (key `state`). */
  function updateState(event) {
    const state = event.type === 'initial_state' ? event.data?.state : event.data;
    const live = event.type !== 'initial_state';
    if (state && updateSystemState(state, event.type || 'websocket', live)) audioDeltas++;
  }

  function applyPosition(payload) {
    const session = systemState.value.session;
    // A playhead is a claim about one session: one for another is dropped.
    if (!session || session.id !== payload.session_id) return;
    systemState.value = { ...systemState.value, session: { ...session, position: payload.position } };
  }

  /** `source/position`: the anchor alone moved (a seek, a drift). */
  function updatePosition(payload) {
    lastPosition = payload;
    applyPosition(payload);
  }

  /** `volume/volume_changed`. */
  function handleVolumeEvent(event) {
    if (applyVolumeState(event.data || {})) volumeDeltas++;
  }

  /** True when a state was applied. */
  function applyVolumeState({ display, state }) {
    let applied = false;
    // Validate with Zod — .catch() defaults handle invalid fields automatically
    if (state) {
      const result = validateSchema(VolumeStateSchema, state, 'VolumeState');

      if (result.success) {
        applied = true;
        volumeState.value.mode = result.data.mode;
        volumeState.value.global_volume_db = result.data.global_volume_db;
        volumeState.value.global_volume = result.data.global_volume;
        volumeState.value.global_mute = result.data.global_mute;
        volumeState.value.volume_control = result.data.volume_control;
        volumeState.value.any_volume_control = result.data.any_volume_control;
        volumeState.value.clients = result.data.clients;
        volumeState.value.zones = result.data.zones;
      }
    }

    if (!state || (display !== 'bar' && display !== 'panel')) return applied;

    // A remote control's press opens the panel on the unit's own screen — it is
    // read from across the room — and the bar on every other screen. The held
    // panel already shows the level, and the press counts as using it.
    if (display === 'panel' && isKiosk()) {
      if (volumePanelOpen.value && !volumePanelPassive.value) armVolumePanelClose();
      else showPassivePanel();
      return applied;
    }

    // Any other change shows the bar, the last gesture deciding how: one made
    // while a remote's panel is up replaces it. Not on the screen holding the
    // Volume panel, which shows the level itself, nor for the echoes of its
    // last drag still arriving after it closed (other screens show them).
    if (volumePanelPassive.value) closeVolumePanel();
    if (!volumePanelOpen.value && !globalInFlight && !barQuietAfterPanel) {
      if (volumeBarHideTimer) clearTimeout(volumeBarHideTimer);
      showVolumeBar.value = true;
      volumeBarHideTimer = setTimeout(() => {
        showVolumeBar.value = false;
      }, VOLUME_SHOWN_MS);
    }
    return applied;
  }

  /**
   * Refill the whole mirror from its two snapshot endpoints.
   *
   * Same uniform recipe as every other delta-fed store: App.vue's resyncStores()
   * calls it on boot, on reconnect and on tab return. It replaces the hand-written
   * copy websocket.js used to run on visibility change, which fetched the same two
   * endpoints and replayed them as synthetic WS envelopes.
   *
   * `display: 'none'` — a heal the user did not cause must not flash the overlay.
   *
   * A whole state or volume delta received while a request was in flight is
   * newer than its answer, so that half is not applied, and the domain counts as
   * current. A playhead is not a whole state: the snapshot still lands, and the
   * last anchor received meanwhile is put back on top of it unless the snapshot
   * carries a later one.
   *
   * A snapshot the schema refuses still counts as the server reached: the
   * refusal is deterministic, so a retry would refuse it again and hold
   * serverSync's second stage back for the whole session. It is reported by
   * updateSystemState instead.
   */
  async function resync() {
    const audioSeen = audioDeltas;
    const volumeSeen = volumeDeltas;
    const positionSeen = lastPosition;
    const [audioRes, volumeRes] = await Promise.all([
      apiCall.get('/api/audio/state', {
        category: 'store',
        message: 'Failed to fetch audio state on resync',
        logLevel: 'warn',
      }),
      apiCall.get('/api/volume/state', {
        category: 'store',
        message: 'Failed to fetch volume state on resync',
        logLevel: 'warn',
      }),
    ]);

    const audioNewer = audioDeltas !== audioSeen;
    if (audioRes.ok && !audioNewer) {
      updateSystemState(audioRes.data, 'resync');
      const anchor = systemState.value.session?.position;
      const snapshotLater = anchor && lastPosition?.position && anchor.at > lastPosition.position.at;
      if (lastPosition !== positionSeen && !snapshotLater) applyPosition(lastPosition);
    }
    const volumeNewer = volumeDeltas !== volumeSeen;
    const volumeOk = volumeRes.ok && volumeRes.data.status === 'success';
    if (volumeOk && !volumeNewer) {
      applyVolumeState({ display: 'none', state: volumeRes.data.data });
    }
    return (audioRes.ok || audioNewer) && (volumeOk || volumeNewer);
  }

  // Dismiss the volume bar on user tap. Cancels the auto-hide timer so it
  // doesn't fire later; idempotent so re-tapping during the fade-out is a no-op.
  function hideVolumeBar() {
    if (volumeBarHideTimer) clearTimeout(volumeBarHideTimer);
    volumeBarHideTimer = null;
    showVolumeBar.value = false;
  }

  // === PER-CLIENT VOLUME / MUTE ===
  // The per-client slice of volumeState above, plus its writes. It lived in
  // equalizerStore because CamillaDSP is what applies the attenuation, but the
  // state is owned here and the endpoints are /api/volume/*: nothing about it is
  // an equalizer. The registry answers "is this client local / online / zoned?".

  /** "dc:a6:32:7e:d3:43" -> "dca6327ed343" — the API's colon-free path segment. */
  function macToUrlFormat(macId) {
    return macId.replace(/:/g, '');
  }

  /** Remote clients only exist as an audio destination while multiroom is on. */
  function _reachable(clientId, what) {
    const registry = useMultiroomStore();
    if (!registry.isClientLocal(clientId) && !systemState.value.multiroom_enabled) {
      logger.warn('store', `Skipping ${what} update for ${clientId} - multiroom disabled`);
      return false;
    }
    return true;
  }

  /**
   * Set one client's volume. Each client's volume is independent — changing one
   * does not affect the others.
   * @param {string} clientId MAC address
   * @param {number} volumeDb -80..0
   */
  async function setClientVolume(clientId, volumeDb) {
    if (!_reachable(clientId, 'volume')) return false;

    const result = await apiCall.patch(
      `/api/volume/client/mac/${macToUrlFormat(clientId)}`,
      { volume_db: volumeDb },
      {
        category: 'store',
        message: `Error updating volume for ${clientId}`,
      },
    );
    return result.ok;
  }

  /**
   * Move a whole zone to a level, in one request.
   *
   * A level, not a delta: the server measures the delta against the average it
   * holds when the request lands. A delta computed here, against an average read
   * before the previous send had been applied, stacked on every send of a drag.
   * @returns {Promise<object>} {status, zone_id, new_average_db, delta_db, applied_to, offline_clients}
   */
  async function setZoneVolume(zoneId, volumeDb) {
    if (!systemState.value.multiroom_enabled) {
      logger.warn('store', 'Skipping zone volume - multiroom disabled');
      return { status: 'error', message: 'Multiroom disabled' };
    }

    const result = await apiCall.patch(`/api/volume/zone/${zoneId}`, { volume_db: volumeDb }, {
      category: 'store',
      message: `Error setting zone volume for ${zoneId}`,
      rethrow: true,
    });
    return result.data;
  }

  /** One client's volume in dB, from the WS-maintained state. */
  function getClientVolume(clientId) {
    return volumeState.value.clients[clientId]?.volume_db ?? DEFAULT_VOLUME_DB;
  }

  // A mute PATCH answers once the speaker took it and the volume state was
  // broadcast — a satellite round trip, up to 5 s behind a volume drag — so a
  // switch drawn from the state alone would sit on the old position meanwhile.
  // Per client and per zone, dropped when its request settles, exactly as
  // `requestedMultiroom`.
  const requestedMute = ref({ clients: {}, zones: {} });
  const muteRequestSeq = { clients: {}, zones: {} };

  /** Run `send` with `muted` shown for every `[kind, id]` of `targets` meanwhile. */
  async function _withRequestedMute(targets, muted, send) {
    const seqs = targets.map(([kind, id]) => (muteRequestSeq[kind][id] = (muteRequestSeq[kind][id] ?? 0) + 1));
    for (const [kind, id] of targets) requestedMute.value[kind][id] = muted;
    try {
      return await send();
    } finally {
      targets.forEach(([kind, id], i) => {
        if (muteRequestSeq[kind][id] === seqs[i]) delete requestedMute.value[kind][id];
      });
    }
  }

  /** One client's mute flag: the request in flight, else the WS-maintained state. */
  function getClientMute(clientId) {
    return requestedMute.value.clients[clientId] ?? volumeState.value.clients[clientId]?.mute ?? false;
  }

  /** Whether a whole zone is muted: the request in flight, else the WS-maintained state. */
  function getZoneMute(zoneId) {
    return requestedMute.value.zones[zoneId] ?? volumeState.value.zones[zoneId]?.all_muted ?? false;
  }

  /** Mute or unmute one client. */
  async function setClientMute(clientId, muted) {
    if (!_reachable(clientId, 'mute')) return false;

    const result = await _withRequestedMute([['clients', clientId]], muted, () => apiCall.patch(
      `/api/volume/client/mac/${macToUrlFormat(clientId)}/mute`,
      { mute: muted },
      {
        category: 'store',
        message: `Error updating mute for ${clientId}`,
      },
    ));
    return result.ok;
  }

  /**
   * Mute or unmute a whole zone, in one request: the server stores it for
   * every member (an offline one takes it when it comes back) and broadcasts
   * once, where one request per member broadcast once each.
   */
  async function setZoneMute(zoneId, muted) {
    if (!systemState.value.multiroom_enabled) {
      logger.warn('store', 'Skipping zone mute - multiroom disabled');
      return false;
    }

    const members = useMultiroomStore().zoneList.find((zone) => zone.id === zoneId)?.client_ids ?? [];
    const targets = [['zones', zoneId], ...members.map((mac) => ['clients', mac])];
    const result = await _withRequestedMute(targets, muted, () => apiCall.patch(
      `/api/volume/zone/${zoneId}/mute`, { mute: muted }, {
        category: 'store',
        message: `Error setting zone mute for ${zoneId}`,
      },
    ));
    return result.ok;
  }

  return {
    resync,
    // State
    systemState,
    multiroomTarget,
    sessionOpening,
    introSessionId,
    volumeState,
    showVolumeBar,
    volumePanelOpen,
    volumePanelPassive,
    commandError,
    transientNotice,

    // Actions
    changeSource,
    disconnectSource,
    isDisconnecting,
    sendCommand,
    setMultiroomEnabled,
    updateState,
    updatePosition,
    adjustVolume,
    setGlobalVolume,
    handleVolumeEvent,
    hideVolumeBar,
    openVolumePanel,
    closeVolumePanel,
    armVolumePanelClose,
    holdVolumePanel,

    // Per-client volume / mute
    getClientVolume,
    getClientMute,
    getZoneMute,
    setClientVolume,
    setClientMute,
    setZoneVolume,
    setZoneMute,
  };
});
