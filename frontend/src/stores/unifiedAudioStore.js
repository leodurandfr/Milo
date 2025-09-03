// frontend/src/stores/unifiedAudioStore.js - Version volume affiché simplifié
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === ÉTAT UNIQUE ===
  const systemState = ref({
    active_source: 'none',
    plugin_state: 'inactive',
    transitioning: false,
    target_source: null,
    metadata: {},
    error: null,
    multiroom_enabled: false,
    equalizer_enabled: false
  });

  // État volume simplifié - UNIQUEMENT volume affiché (0-100%)
  const volumeState = ref({
    currentVolume: 0
    // Plus de limits - les limites ALSA sont gérées côté backend uniquement
  });

  let volumeBarRef = null;
  let lastWebSocketUpdate = 0;

  // === GETTERS ===
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  const error = computed(() => systemState.value.error);
  const multiroomEnabled = computed(() => systemState.value.multiroom_enabled);
  const equalizerEnabled = computed(() => systemState.value.equalizer_enabled);

  const displayedSource = computed(() => {
    if (systemState.value.transitioning && systemState.value.target_source) {
      return systemState.value.target_source;
    }
    return systemState.value.active_source;
  });

  // Volume getter simplifié
  const currentVolume = computed(() => volumeState.value.currentVolume);

  // === ACTIONS AUDIO (inchangées) ===
  async function changeSource(source) {
    try {
      console.log('🚀 CHANGING SOURCE TO:', source);
      const response = await axios.post(`/api/audio/source/${source}`);
      const success = response.data.status === 'success';
      console.log('🚀 CHANGE SOURCE RESPONSE:', success);
      return success;
    } catch (err) {
      console.error('Change source error:', err);
      return false;
    }
  }

  async function sendCommand(source, command, data = {}) {
    try {
      const response = await axios.post(`/api/audio/control/${source}`, {
        command,
        data
      });
      return response.data.status === 'success';
    } catch (err) {
      console.error(`Command error (${source}/${command}):`, err);
      return false;
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

  async function setEqualizerEnabled(enabled) {
    try {
      const response = await axios.post(`/api/routing/equalizer/${enabled}`);
      return response.data.status === 'success';
    } catch (err) {
      console.error('Set equalizer error:', err);
      return false;
    }
  }

  // === ACTIONS VOLUME SIMPLIFIÉES ===
  async function setVolume(volume, showBar = true) {
    try {
      const response = await axios.post('/api/volume/set', {
        volume,
        show_bar: showBar
      });

      if (response.data.status === 'success') {
        volumeState.value.currentVolume = response.data.volume;
        return true;
      }
      return false;

    } catch (error) {
      console.error('Error setting volume:', error);
      return false;
    }
  }

  async function adjustVolume(delta, showBar = true) {
    fetch('/api/volume/adjust', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delta, show_bar: showBar })
    }).catch(error => {
      console.error('Erreur volume:', error);
    });
  }

  async function increaseVolume() {
    return await adjustVolume(5);
  }

  async function decreaseVolume() {
    return await adjustVolume(-5);
  }

  // === REFRESH (PROTECTION ANTI-ÉCRASEMENT) ===
  async function refreshState() {
    try {
      // console.log('🔄 Refreshing unified state...');

      if (systemState.value.transitioning) {
        // console.log('⚠️ Skipping refresh - transition in progress');
        return true;
      }

      const now = Date.now();
      if (lastWebSocketUpdate && (now - lastWebSocketUpdate) < 1000) {
        // console.log('⚠️ Skipping refresh - recent WebSocket update');
        return true;
      }

      // État audio
      if (systemState.value.active_source === 'librespot') {
        try {
          const response = await axios.get('/librespot/fresh-status');
          if (response.data?.status === 'success') {
            const freshMetadata = response.data.fresh_metadata || {};
            systemState.value.metadata = freshMetadata;
            systemState.value.plugin_state = response.data.device_connected ? 'connected' : 'ready';
            console.log('✅ Fresh librespot data updated');
          } else {
            const audioResponse = await axios.get('/api/audio/state');
            if (audioResponse.data) {
              updateSystemState(audioResponse.data, 'http_refresh');
            }
          }
        } catch (freshApiError) {
          console.warn('⚠️ Fresh-status fallback to main API');
          const audioResponse = await axios.get('/api/audio/state');
          if (audioResponse.data) {
            updateSystemState(audioResponse.data, 'http_fallback');
          }
        }
      } else {
        const audioResponse = await axios.get('/api/audio/state');
        if (audioResponse.data) {
          updateSystemState(audioResponse.data, 'http_refresh');
        }
      }

      // État volume (simplifié)
      const volumeResponse = await axios.get('/api/volume/status');
      if (volumeResponse.data?.status === 'success') {
        const data = volumeResponse.data.data;
        volumeState.value.currentVolume = data.volume || 0;
        // Plus de gestion des limites - tout est en 0-100%
      }

      // console.log('✅ Unified state refreshed');
      return true;

    } catch (error) {
      console.error('❌ Error refreshing state:', error);
      return false;
    }
  }

  // === GESTION VISIBILITÉ (inchangée) ===
  function setupVisibilityListener() {
    const visibilityHandler = () => {
      if (!document.hidden) {
        setTimeout(() => {
          if (!systemState.value.transitioning) {
            refreshState();
          }
        }, 1000);
      }
    };

    document.addEventListener('visibilitychange', visibilityHandler);
    window.addEventListener('focus', visibilityHandler);

    return () => {
      document.removeEventListener('visibilitychange', visibilityHandler);
      window.removeEventListener('focus', visibilityHandler);
    };
  }

  // === MISE À JOUR D'ÉTAT  ===
  function updateSystemState(newState, source = 'unknown') {
    // console.log('🐛 UPDATE SYSTEM STATE - Source:', source);
    // console.log('🐛 NEW STATE MULTIROOM:', newState.multiroom_enabled);
    // console.log('🐛 NEW STATE EQUALIZER:', newState.equalizer_enabled);

    // console.log('🐛 AVANT UPDATE - systemState:', JSON.stringify(systemState.value));

    systemState.value = {
      active_source: newState.active_source || 'none',
      plugin_state: newState.plugin_state || 'inactive',
      transitioning: newState.transitioning || false,
      target_source: newState.target_source || null,
      metadata: newState.metadata || {},
      error: newState.error || null,
      multiroom_enabled: newState.multiroom_enabled !== undefined ? newState.multiroom_enabled : systemState.value.multiroom_enabled,
      equalizer_enabled: newState.equalizer_enabled !== undefined ? newState.equalizer_enabled : systemState.value.equalizer_enabled
    };

    // console.log('🐛 APRÈS UPDATE - systemState:', JSON.stringify(systemState.value));
  }

  function updateState(event) {
    // console.log('🌐 WEBSOCKET EVENT:', event);

    if (event.data?.full_state) {
      lastWebSocketUpdate = Date.now();
      updateSystemState(event.data.full_state, 'websocket');
    }
  }

  function handleVolumeEvent(event) {
    if (event.data && typeof event.data.volume === 'number') {
      // Volume simplifié - toujours 0-100%
      volumeState.value.currentVolume = event.data.volume;

      // Afficher la barre pour tous les changements de volume
      if (volumeBarRef && volumeBarRef.value) {
        try {
          volumeBarRef.value.showVolume();
        } catch (error) {
          console.warn('Failed to show volume bar:', error);
        }
      }
    }
  }

  function setVolumeBarRef(reactiveRef) {
    volumeBarRef = reactiveRef;
  }

  return {
    // État
    systemState,
    volumeState,

    // Getters
    currentSource,
    pluginState,
    isTransitioning,
    metadata,
    error,
    multiroomEnabled,
    equalizerEnabled,
    displayedSource,
    currentVolume,

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
    setVolumeBarRef,
    refreshState,
    setupVisibilityListener
  };
});