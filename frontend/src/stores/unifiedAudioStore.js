// frontend/src/stores/unifiedAudioStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { AudioStateSchema, VolumeStateSchema, validateSchema } from '@/schemas/api';
import { DEFAULT_VOLUME_DB, MAX_ADJUST_DB } from '@/constants/volume';

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

  async function setMultiroomEnabled(enabled) {
    const result = await apiCall.put('/api/routing/multiroom', { enabled }, {
      category: 'store',
      message: 'Set multiroom failed',
      checkStatus: true,
    });
    return result.ok;
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

  // === STATE UPDATE ===
  // Strict: a state that does not parse is refused whole and the last good one
  // stays, with a warning in the journal — never half-applied with defaults.
  function updateSystemState(newState, origin = 'unknown') {
    const result = validateSchema(AudioStateSchema, newState, `AudioState from ${origin}`);
    if (!result.success) {
      logger.warn('store', `Audio state from ${origin} refused: it does not match the wire`,
        result.error?.issues);
      return;
    }
    systemState.value = result.data;
  }

  /** `source/state` (its data IS the state) and `system/initial_state` (key `state`). */
  function updateState(event) {
    const state = event.type === 'initial_state' ? event.data?.state : event.data;
    if (state) updateSystemState(state, event.type || 'websocket');
  }

  /** `source/position`: the anchor alone moved (a seek, a speed change, a drift). */
  function updatePosition(payload) {
    const session = systemState.value.session;
    // A playhead is a claim about one session: one for another is dropped.
    if (!session || session.id !== payload.session_id) return;
    systemState.value = { ...systemState.value, session: { ...session, position: payload.position } };
  }

  function handleVolumeEvent(event) {
    const { show_bar, state } = event.data || {};

    // Validate with Zod — .catch() defaults handle invalid fields automatically
    if (state) {
      const result = validateSchema(VolumeStateSchema, state, 'VolumeState');

      if (result.success) {
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

    // Show volume bar and auto-hide after 3 seconds
    if (show_bar !== false && state) {
      if (volumeBarHideTimer) clearTimeout(volumeBarHideTimer);
      showVolumeBar.value = true;
      volumeBarHideTimer = setTimeout(() => {
        showVolumeBar.value = false;
      }, 3000);
    }
  }

  /**
   * Refill the whole mirror from its two snapshot endpoints.
   *
   * Same uniform recipe as every other delta-fed store: App.vue's resyncStores()
   * calls it on boot, on reconnect and on tab return. It replaces the hand-written
   * copy websocket.js used to run on visibility change, which fetched the same two
   * endpoints and replayed them as synthetic WS envelopes.
   *
   * `show_bar: false` — a heal the user did not cause must not flash the overlay.
   */
  async function resync() {
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

    if (audioRes.ok) {
      updateSystemState(audioRes.data, 'resync');
    }
    const volumeOk = volumeRes.ok && volumeRes.data.status === 'success';
    if (volumeOk) {
      handleVolumeEvent({ data: { show_bar: false, state: volumeRes.data.data } });
    }
    return audioRes.ok && volumeOk;
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

  /** One client's mute flag, from the WS-maintained state. */
  function getClientMute(clientId) {
    return volumeState.value.clients[clientId]?.mute ?? false;
  }

  /** Mute or unmute one client. */
  async function setClientMute(clientId, muted) {
    if (!_reachable(clientId, 'mute')) return false;

    const result = await apiCall.patch(
      `/api/volume/client/mac/${macToUrlFormat(clientId)}/mute`,
      { mute: muted },
      {
        category: 'store',
        message: `Error updating mute for ${clientId}`,
      },
    );
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

    const result = await apiCall.patch(`/api/volume/zone/${zoneId}/mute`, { mute: muted }, {
      category: 'store',
      message: `Error setting zone mute for ${zoneId}`,
    });
    return result.ok;
  }

  return {
    resync,
    // State
    systemState,
    volumeState,
    showVolumeBar,
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
    handleVolumeEvent,
    hideVolumeBar,

    // Per-client volume / mute
    getClientVolume,
    getClientMute,
    setClientVolume,
    setClientMute,
    setZoneVolume,
    setZoneMute,
  };
});
