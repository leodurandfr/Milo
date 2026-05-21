// frontend/src/stores/unifiedAudioStore.js - Cleaned version without UI states
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useSettingsStore } from '@/stores/settingsStore';
import { SystemStateSchema, VolumeStateSchema, validateSchema } from '@/schemas/api';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === SINGLE SYSTEM STATE ===
  const systemState = ref({
    active_source: 'none',
    source_state: 'waiting',
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

  // Loading states for async operations
  const isChangingSource = ref(false);
  const isSendingCommand = ref(false);

  // Transient command error (set on sendCommand failure, consumed by App.vue)
  const commandError = ref(null);


  // === AUDIO ACTIONS ===
  async function changeSource(source) {
    isChangingSource.value = true;
    try {
      const result = await apiCall.post(`/api/audio/source/${source}`, null, {
        category: 'store',
        message: `Change source failed: ${source}`,
        checkStatus: true,
      });
      return result.ok;
    } finally {
      isChangingSource.value = false;
    }
  }

  async function sendCommand(source, command, data = {}) {
    isSendingCommand.value = true;
    try {
      const result = await apiCall.post(`/api/audio/control/${source}`, { command, data }, {
        category: 'store',
        message: `Command failed: ${source}/${command}`,
        checkStatus: true,
      });
      if (!result.ok) {
        commandError.value = { source, command };
      }
      return result.ok;
    } finally {
      isSendingCommand.value = false;
    }
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
      systemState.value = {
        active_source: result.data.active_source,
        source_state: result.data.source_state,
        transitioning: result.data.transitioning,
        metadata: result.data.metadata || {},
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

  return {
    // State
    systemState,
    volumeState,
    showVolumeBar,
    commandError,

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
  };
});
