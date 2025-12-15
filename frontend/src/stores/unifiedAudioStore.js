// frontend/src/stores/unifiedAudioStore.js - Cleaned version without UI states
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';
import { useSettingsStore } from './settingsStore';
import { useDspStore } from './dspStore';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === SINGLE SYSTEM STATE ===
  const systemState = ref({
    active_source: 'none',
    plugin_state: 'inactive',
    transitioning: false,
    target_source: null,
    metadata: {},
    error: null,
    multiroom_enabled: false,
    dsp_effects_enabled: false
  });

  // === VOLUME STATE (in dB) ===
  const volumeState = ref({
    volume_db: -30.0,          // Current volume in dB (-60 to 0)
    step_mobile_db: 3.0        // Volume step for mobile buttons
  });

  // Volume bar visibility state (replaces component coupling)
  const showVolumeBar = ref(false);
  let volumeBarHideTimer = null;

  // Loading states for async operations
  const isChangingSource = ref(false);
  const isSendingCommand = ref(false);

  let lastWebSocketUpdate = 0;


  // === AUDIO ACTIONS ===
  async function changeSource(source) {
    isChangingSource.value = true;
    try {
      console.log('🚀 CHANGING SOURCE TO:', source);
      const response = await axios.post(`/api/audio/source/${source}`);
      const success = response.data.status === 'success';
      console.log('🚀 CHANGE SOURCE RESPONSE:', success);
      return success;
    } catch (err) {
      console.error('Change source error:', err);
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
      console.error(`Command error (${source}/${command}):`, err);
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
      console.error('Set multiroom error:', err);
      return false;
    }
  }

  async function setDspEnabled(enabled) {
    try {
      const response = await axios.post(`/api/routing/dsp/${enabled}`);
      return response.data.status === 'success';
    } catch (err) {
      console.error('Set DSP error:', err);
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
        volumeState.value.volume_db = response.data.volume_db;
        return true;
      }
      return false;

    } catch (error) {
      console.error('Error setting volume:', error);
      return false;
    }
  }

  async function adjustVolume(delta_db, showBar = true) {
    try {
      const response = await axios.post('/api/volume/adjust', { delta_db, show_bar: showBar });
      if (response.data.status === 'success') {
        // Apply delta locally for immediate UI update (before WebSocket broadcast arrives)
        const { useDspStore } = await import('./dspStore');
        const dspStore = useDspStore();
        dspStore.applyGlobalDelta(delta_db);
      }
      return response.data.status === 'success';
    } catch (error) {
      console.error('Error adjusting volume:', error);
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

  // === VISIBILITY MANAGEMENT ===
  // Note: No need for polling when returning to focus – WebSocket keeps state up to date
  function setupVisibilityListener() {
    let lastVisibilityLog = 0;
    const DEBOUNCE_MS = 100; // Prevent duplicate logs from visibilitychange + focus firing together

    const visibilityHandler = () => {
      if (!document.hidden) {
        const now = Date.now();
        if (now - lastVisibilityLog > DEBOUNCE_MS) {
          lastVisibilityLog = now;
          console.log('ℹ️ App visible - state already synchronized via WebSocket');
        }
      }
    };

    document.addEventListener('visibilitychange', visibilityHandler, { passive: true });
    window.addEventListener('focus', visibilityHandler, { passive: true });

    return () => {
      document.removeEventListener('visibilitychange', visibilityHandler, { passive: true });
      window.removeEventListener('focus', visibilityHandler, { passive: true });
    };
  }

  // === STATE UPDATE ===
  function updateSystemState(newState, source = 'unknown') {
    // Defensive validation of values received from WebSocket
    // Note: This validation is a defensive frontend safety measure.
    // It should never be needed if the backend works correctly,
    // but it protects against data corruption in transit.
    const validSources = ['none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast'];
    const validStates = ['inactive', 'ready', 'connected', 'error'];

    // Validate active_source
    const activeSource = validSources.includes(newState.active_source)
      ? newState.active_source
      : 'none';

    // Validate plugin_state
    const pluginState = validStates.includes(newState.plugin_state)
      ? newState.plugin_state
      : 'inactive';

    // Validate transitioning (must be boolean)
    const transitioning = typeof newState.transitioning === 'boolean'
      ? newState.transitioning
      : false;

    // Validate target_source
    const targetSource = newState.target_source && validSources.includes(newState.target_source)
      ? newState.target_source
      : null;

    // Validate metadata (must be object)
    const metadata = newState.metadata && typeof newState.metadata === 'object' && !Array.isArray(newState.metadata)
      ? newState.metadata
      : {};

    // Validate multiroom_enabled and dsp_effects_enabled (must be boolean)
    const multiroomEnabled = typeof newState.multiroom_enabled === 'boolean'
      ? newState.multiroom_enabled
      : systemState.value.multiroom_enabled;

    const dspEffectsEnabled = typeof newState.dsp_effects_enabled === 'boolean'
      ? newState.dsp_effects_enabled
      : systemState.value.dsp_effects_enabled;

    systemState.value = {
      active_source: activeSource,
      plugin_state: pluginState,
      transitioning: transitioning,
      target_source: targetSource,
      metadata: metadata,
      error: newState.error || null,
      multiroom_enabled: multiroomEnabled,
      dsp_effects_enabled: dspEffectsEnabled
    };

    // Log warning if validation changed values
    if (activeSource !== newState.active_source || pluginState !== newState.plugin_state) {
      console.warn('⚠️ Invalid WebSocket data received:', {
        received: { active_source: newState.active_source, plugin_state: newState.plugin_state },
        corrected: { active_source: activeSource, plugin_state: pluginState },
        source
      });
    }
  }

  function updateState(event) {
    if (event.data?.full_state) {
      lastWebSocketUpdate = Date.now();
      updateSystemState(event.data.full_state, 'websocket');
    }
  }

  function handleVolumeEvent(event) {
    if (event.data && typeof event.data.volume_db === 'number') {
      volumeState.value.volume_db = event.data.volume_db;

      // Update step if provided
      if (typeof event.data.step_mobile_db === 'number') {
        volumeState.value.step_mobile_db = event.data.step_mobile_db;
      }

      // Sync client volumes to dspStore (always includes 'local', plus remote clients in multiroom)
      if (event.data.client_volumes) {
        try {
          const dspStore = useDspStore();
          for (const [hostname, volumeDb] of Object.entries(event.data.client_volumes)) {
            dspStore.setClientDspVolume(hostname, volumeDb);
          }
        } catch (e) {
          // DSP store may not be initialized yet
        }
      }

      // Show volume bar and auto-hide after 3 seconds (only if show_bar is true or not specified)
      if (event.data.show_bar !== false) {
        if (volumeBarHideTimer) clearTimeout(volumeBarHideTimer);
        showVolumeBar.value = true;
        volumeBarHideTimer = setTimeout(() => {
          showVolumeBar.value = false;
        }, 3000);
      }
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
    setDspEnabled,
    updateState,
    setVolume,
    adjustVolume,
    increaseVolume,
    decreaseVolume,
    handleVolumeEvent,
    hideVolumeBar,
    setupVisibilityListener
  };
});
