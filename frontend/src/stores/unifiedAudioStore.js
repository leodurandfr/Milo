// frontend/src/stores/unifiedAudioStore.js - Cleaned version without UI states
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { useSettingsStore } from './settingsStore';
import { logger } from '@/services/logger';
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
      const response = await axios.post(`/api/audio/control/${source}`, {
        command,
        data
      });
      return response.data.status === 'success';
    } catch (err) {
      logger.error('store', `Command failed: ${source}/${command}`, { error: err.message });
      return false;
    } finally {
      isSendingCommand.value = false;
    }
  }

  async function setMultiroomEnabled(enabled) {
    try {
      const response = await axios.post(`/api/routing/multiroom/${enabled}`);
      return response.data.status === 'success';
    } catch (err) {
      logger.error('store', 'Set multiroom failed', { enabled, error: err.message });
      return false;
    }
  }

  async function setEqualizerEnabled(enabled) {
    try {
      const response = await axios.put('/api/equalizer/enabled', { enabled });
      return response.data.status === 'success';
    } catch (err) {
      logger.error('store', 'Set equalizer failed', { enabled, error: err.message });
      return false;
    }
  }

  // === VOLUME ACTIONS (all in dB) ===
  async function setVolume(volume_db, showBar = true) {
    try {
      const response = await axios.post('/api/volume/set', {
        volume_db,
        show_bar: showBar
      });

      if (response.data.status === 'success') {
        // Volume state will be updated via WebSocket broadcast
        return true;
      }
      return false;

    } catch (error) {
      logger.error('store', 'Set volume failed', { volume_db, error: error.message });
      return false;
    }
  }

  async function adjustVolume(delta_db, showBar = true) {
    try {
      const response = await axios.post('/api/volume/adjust', { delta_db, show_bar: showBar });
      // Volume state will be updated via WebSocket broadcast
      return response.data.status === 'success';
    } catch (error) {
      logger.error('store', 'Adjust volume failed', { delta_db, error: error.message });
      return false;
    }
  }

  async function increaseVolume() {
    const step = volumeState.value.step_mobile_db || 3.0;
    return await adjustVolume(step);
  }

  async function decreaseVolume() {
    const step = volumeState.value.step_mobile_db || 3.0;
    return await adjustVolume(-step);
  }

  // === WEBSOCKET STATE UPDATES ===
  // State is now received exclusively via WebSocket (initial_state and state_changed events)
  // The WebSocket handshake ensures initial state is sent when the client is ready

  // === STATE UPDATE ===
  function updateSystemState(newState, source = 'unknown') {
    // Validate incoming state using zod schema
    const result = validateSchema(SystemStateSchema, newState, `SystemState from ${source}`);

    if (result.success) {
      // Schema validation passed - use validated data (explicitly pick only used properties)
      systemState.value = {
        active_source: result.data.active_source,
        plugin_state: result.data.plugin_state,
        transitioning: result.data.transitioning,
        metadata: result.data.metadata || {},
        error: result.data.error || null,
        multiroom_enabled: result.data.multiroom_enabled
      };
    } else {
      // Schema validation failed - apply safe fallbacks
      logger.warn('store', 'SystemState validation failed, applying fallbacks', {
        source,
        errors: result.error.issues.map(i => `${i.path.join('.')}: ${i.message}`)
      });

      // Fallback to safe defaults for invalid fields
      const validSources = ['none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast', 'airplay'];
      const validStates = ['starting', 'ready', 'connected', 'error'];

      systemState.value = {
        active_source: validSources.includes(newState.active_source) ? newState.active_source : 'none',
        plugin_state: validStates.includes(newState.plugin_state) ? newState.plugin_state : 'ready',
        transitioning: typeof newState.transitioning === 'boolean' ? newState.transitioning : false,
        metadata: (newState.metadata && typeof newState.metadata === 'object') ? newState.metadata : {},
        error: newState.error || null,
        multiroom_enabled: typeof newState.multiroom_enabled === 'boolean'
          ? newState.multiroom_enabled : systemState.value.multiroom_enabled
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

    // Update unified volume state with schema validation
    if (state) {
      const result = validateSchema(VolumeStateSchema, state, 'VolumeState');

      if (result.success) {
        volumeState.value.mode = result.data.mode;
        volumeState.value.global_volume_db = result.data.global_volume_db;
        volumeState.value.global_mute = result.data.global_mute;
        volumeState.value.clients = result.data.clients;
        volumeState.value.zones = result.data.zones;
      } else {
        // Fallback to direct assignment with defaults
        logger.debug('store', 'VolumeState validation partial, using fallbacks');
        volumeState.value.mode = state.mode || 'direct';
        volumeState.value.global_volume_db = state.global_volume_db ?? -60.0;
        volumeState.value.global_mute = state.global_mute ?? false;
        volumeState.value.clients = state.clients || {};
        volumeState.value.zones = state.zones || {};
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

  function hideVolumeBar() {
    if (volumeBarHideTimer) clearTimeout(volumeBarHideTimer);
    showVolumeBar.value = false;
  }

  return {
    // State
    systemState,
    volumeState,
    showVolumeBar,
    isChangingSource,
    isSendingCommand,

    // Actions
    changeSource,
    sendCommand,
    setMultiroomEnabled,
    setEqualizerEnabled,
    updateState,
    setVolume,
    adjustVolume,
    increaseVolume,
    decreaseVolume,
    handleVolumeEvent,
    hideVolumeBar
  };
});
