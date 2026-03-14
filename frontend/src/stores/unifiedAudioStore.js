// frontend/src/stores/unifiedAudioStore.js - Cleaned version without UI states
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useSettingsStore } from '@/stores/settingsStore';
import { SystemStateSchema, VolumeStateSchema, validateSchema } from '@/schemas/api';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === SINGLE SYSTEM STATE ===
  const systemState = ref({
    active_source: 'none',
    plugin_state: 'ready',
    transitioning: false,
    metadata: {},
    error: null,
    multiroom_enabled: false
  });

  // === VOLUME STATE (unified structure) ===
  const volumeState = ref({
    mode: 'direct',                  // 'direct' or 'multiroom'
    global_volume_db: -60.0,         // Global volume (average of unmuted clients)
    global_mute: false,              // Global mute state
    clients: {},                     // {hostname: {volume_db, offset_db, mute, available}}
    zones: {},                       // {zoneId: {id, name, client_ids, average_volume_db, all_muted}}
    step_mobile_db: 3.0              // Volume step for mobile buttons
  });

  // Volume bar visibility state (replaces component coupling)
  const showVolumeBar = ref(false);
  let volumeBarHideTimer = null;

  // Loading states for async operations
  const isChangingSource = ref(false);
  const isSendingCommand = ref(false);


  // === AUDIO ACTIONS ===
  async function changeSource(source) {
    isChangingSource.value = true;
    try {
      logger.store('unifiedAudio', 'changeSource', { source });
      const response = await axios.post(`/api/audio/source/${source}`);
      const success = response.data.status === 'success';
      logger.api('POST', `/api/audio/source/${source}`, { status: response.status, success });
      return success;
    } catch (err) {
      logger.error('store', 'Change source failed', { source, error: err.message });
      return false;
    } finally {
      isChangingSource.value = false;
    }
  }

  async function sendCommand(source, command, data = {}) {
    isSendingCommand.value = true;
    try {
      return await apiCall('store', `Command failed: ${source}/${command}`, async () => {
        const response = await axios.post(`/api/audio/control/${source}`, { command, data });
        return response.data.status === 'success';
      });
    } finally {
      isSendingCommand.value = false;
    }
  }

  async function setMultiroomEnabled(enabled) {
    return apiCall('store', 'Set multiroom failed', async () => {
      const response = await axios.put('/api/routing/multiroom', { enabled });
      return response.data.status === 'success';
    });
  }

  // === DISCONNECT ===
  const disconnectingStates = ref({});

  async function disconnectSource(source) {
    if (!source || source === 'none') return false;
    disconnectingStates.value[source] = true;

    const success = await apiCall('store', `Disconnect ${source} failed`, async () => {
      switch (source) {
        case 'bluetooth':
          await axios.post('/api/bluetooth/disconnect');
          return true;
        case 'mac':
          return true;
        default:
          logger.warn('store', `Disconnect not supported for ${source}`);
          return false;
      }
    });

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
    return apiCall('store', 'Adjust volume failed', async () => {
      const response = await axios.post('/api/volume/adjust', { delta_db, show_bar: showBar });
      return response.data.status === 'success';
    });
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
        plugin_state: result.data.plugin_state,
        transitioning: result.data.transitioning,
        metadata: result.data.metadata || {},
        error: result.data.error || null,
        multiroom_enabled: result.data.multiroom_enabled
      };
    }
  }

  function updateState(event) {
    if (event.data?.full_state) {
      updateSystemState(event.data.full_state, 'websocket');
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

  return {
    // State
    systemState,
    volumeState,
    showVolumeBar,

    // Actions
    changeSource,
    disconnectSource,
    isDisconnecting,
    sendCommand,
    setMultiroomEnabled,
    updateState,
    adjustVolume,
    startVolumeInterpolation,
    stopVolumeInterpolation,
    handleVolumeEvent,
  };
});
