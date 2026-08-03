// frontend/src/stores/unifiedAudioStore.js - Cleaned version without UI states
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useSettingsStore } from '@/stores/settingsStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { SystemStateSchema, VolumeStateSchema, validateSchema } from '@/schemas/api';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === SINGLE SYSTEM STATE ===
  const systemState = ref({
    active_source: 'none',
    source_state: 'ready',
    transitioning: false,
    metadata: {},
    error: null,
    multiroom_enabled: false,
    equalizer_effects_enabled: false
  });

  // === VOLUME STATE (unified structure) ===
  const volumeState = ref({
    mode: 'direct',                  // 'direct' or 'multiroom'
    global_volume_db: -45.0,         // Global volume (average of unmuted clients)
    global_mute: false,              // Global mute state
    volume_control: true,            // False = DAC mode (external amp manages volume)
    any_volume_control: true,        // True if any device manages volume via Milo
    clients: {},                     // {hostname: {volume_db, offset_db, mute, available}}
    zones: {},                       // {zoneId: {id, name, client_ids, average_volume_db, all_muted}}
    step_mobile_db: 2.0              // Volume step for mobile buttons
  });

  // Volume bar visibility state (replaces component coupling)
  const showVolumeBar = ref(false);
  let volumeBarHideTimer = null;

  // performance.now() timestamp of when metadata.position last *changed*. Lets a
  // freshly-created position consumer (e.g. the Lyrics modal opened mid-song)
  // compensate for how stale the last broadcast is — position events are periodic
  // and source-dependent (AirPlay only every 30s), so the stored value can lag by
  // seconds. Updated only on a real value change so it tracks the true reading age.
  const positionTimestamp = ref(0);

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
      // Family A source: disconnect flows through the generic control endpoint
      // (the dedicated /api/bluetooth router was retired).
      success = await sendCommand('bluetooth', 'disconnect');
    } else if (source === 'mac') {
      success = true;
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
  // Smooth rAF interpolation for visual volume during hold
  let _rafId = null;
  let _rafLastTime = 0;
  let _rafVelocity = 0; // dB per ms

  function startVolumeInterpolation(delta_db, intervalMs) {
    _rafVelocity = delta_db / intervalMs;
    if (_rafId) return; // already running
    _rafLastTime = performance.now();
    const { min_db, max_db } = useSettingsStore().volumeLimits;
    const tick = (now) => {
      const dt = now - _rafLastTime;
      _rafLastTime = now;
      volumeState.value.global_volume_db = Math.max(min_db, Math.min(max_db,
        volumeState.value.global_volume_db + _rafVelocity * dt));
      _rafId = requestAnimationFrame(tick);
    };
    _rafId = requestAnimationFrame(tick);
  }

  function stopVolumeInterpolation() {
    if (_rafId) {
      cancelAnimationFrame(_rafId);
      _rafId = null;
    }
    _rafVelocity = 0;
  }

  async function adjustVolume(delta_db, showBar = true) {
    const result = await apiCall.post('/api/volume/adjust', { delta_db, show_bar: showBar }, {
      category: 'store',
      message: 'Adjust volume failed',
      checkStatus: true,
    });
    return result.ok;
  }

  // === WEBSOCKET STATE UPDATES ===
  // State is now received exclusively via WebSocket (initial_state and state_changed events)
  // The WebSocket handshake ensures initial state is sent when the client is ready

  // === STATE UPDATE ===
  function updateSystemState(newState, source = 'unknown') {
    // Validate with Zod — .catch() defaults handle invalid fields automatically
    const result = validateSchema(SystemStateSchema, newState, `SystemState from ${source}`);

    if (result.success) {
      const newMetadata = result.data.metadata || {};
      if (newMetadata.position !== systemState.value.metadata?.position) {
        positionTimestamp.value = performance.now();
      }
      systemState.value = {
        active_source: result.data.active_source,
        source_state: result.data.source_state,
        transitioning: result.data.transitioning,
        metadata: newMetadata,
        error: result.data.error || null,
        multiroom_enabled: result.data.multiroom_enabled,
        equalizer_effects_enabled: result.data.equalizer_effects_enabled
      };
    }
  }

  function updateState(event) {
    if (event.data?.full_state) {
      updateSystemState(event.data.full_state, 'websocket');
    }
  }

  function updatePosition(payload) {
    // Ignore stale events from a previous source during transitions
    if (payload.source !== systemState.value.active_source) return;
    if (systemState.value.metadata) {
      if (payload.position !== systemState.value.metadata.position) {
        positionTimestamp.value = performance.now();
      }
      systemState.value.metadata.position = payload.position;
      systemState.value.metadata.duration = payload.duration;
    }
  }

  function handleVolumeEvent(event) {
    const { show_bar, step_mobile_db, state } = event.data || {};

    // Validate with Zod — .catch() defaults handle invalid fields automatically
    if (state) {
      const result = validateSchema(VolumeStateSchema, state, 'VolumeState');

      if (result.success) {
        volumeState.value.mode = result.data.mode;
        volumeState.value.global_volume_db = result.data.global_volume_db;
        volumeState.value.global_mute = result.data.global_mute;
        volumeState.value.volume_control = result.data.volume_control;
        volumeState.value.any_volume_control = result.data.any_volume_control;
        volumeState.value.clients = result.data.clients;
        volumeState.value.zones = result.data.zones;
      }
    }

    // Update step if provided
    if (typeof step_mobile_db === 'number') {
      volumeState.value.step_mobile_db = step_mobile_db;
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

  function updateMobileStep(stepDb) {
    if (typeof stepDb === 'number') {
      volumeState.value.step_mobile_db = stepDb;
    }
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
   * Apply a volume delta to a whole zone in one request.
   *
   * Eliminates a race: N parallel per-client requests produce N stale broadcasts
   * and a flickering slider; one request produces one correct broadcast.
   * @returns {Promise<object>} {status, zone_id, new_average_db, delta_db, applied_to, offline_clients}
   */
  async function applyZoneVolumeDelta(zoneId, deltaDb) {
    if (!systemState.value.multiroom_enabled) {
      logger.warn('store', 'Skipping zone delta - multiroom disabled');
      return { status: 'error', message: 'Multiroom disabled' };
    }

    const result = await apiCall.patch(`/api/volume/zone/${zoneId}`, { delta_db: deltaDb }, {
      category: 'store',
      message: `Error applying zone delta for ${zoneId}`,
      rethrow: true,
    });
    return result.data;
  }

  /** One client's volume in dB, from the WS-maintained state. */
  function getClientVolume(clientId) {
    return volumeState.value.clients[clientId]?.volume_db ?? -30;
  }

  /** One client's mute flag, from the WS-maintained state. */
  function getClientMute(clientId) {
    return volumeState.value.clients[clientId]?.mute ?? false;
  }

  /**
   * Mute or unmute one client. With `{ propagate: true }` the whole zone follows
   * (online members only — an offline one picks it up on reconnect).
   */
  async function setClientMute(clientId, muted, options = {}) {
    const { propagate = false } = options;
    if (!_reachable(clientId, 'mute')) return false;

    const primary = await apiCall.patch(
      `/api/volume/client/mac/${macToUrlFormat(clientId)}/mute`,
      { mute: muted },
      {
        category: 'store',
        message: `Error updating mute for ${clientId}`,
      },
    );
    if (!primary.ok) return false;

    if (propagate) {
      const registry = useMultiroomStore();
      const linkedIds = registry.getLinkedClientIds(clientId);
      if (linkedIds.length > 1) {
        const otherClients = linkedIds.filter(id =>
          id !== clientId && registry.isClientOnline(id)
        );
        await Promise.all(otherClients.map(targetId =>
          apiCall.patch(
            `/api/volume/client/mac/${macToUrlFormat(targetId)}/mute`,
            { mute: muted },
            {
              category: 'store',
              message: `Error propagating mute to ${targetId}`,
            },
          ),
        ));
      }
    }

    return true;
  }

  return {
    // State
    systemState,
    volumeState,
    positionTimestamp,
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
    startVolumeInterpolation,
    stopVolumeInterpolation,
    handleVolumeEvent,
    updateMobileStep,
    hideVolumeBar,

    // Per-client volume / mute
    getClientVolume,
    getClientMute,
    setClientVolume,
    setClientMute,
    applyZoneVolumeDelta,
  };
});
