// frontend/src/stores/unifiedAudioStore.js - Version nettoyée sans états UI
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === ÉTAT SYSTÈME UNIQUE ===
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

  // === ÉTAT VOLUME ===
  const volumeState = ref({
    currentVolume: 0
  });

  let volumeBarRef = null;
  let lastWebSocketUpdate = 0;

  // === GETTERS ===
  // Note: Tous les getters ont été supprimés car ils étaient de simples alias.
  // Utilisez directement systemState.active_source, systemState.metadata, etc.

  // === ACTIONS AUDIO ===
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

  // === ACTIONS VOLUME ===
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

  // === REFRESH (WebSocket uniquement - pas de polling HTTP) ===
  // Note: L'état est synchronisé automatiquement via WebSocket.
  // Cette fonction est conservée uniquement pour compatibilité mais ne fait plus de polling HTTP.
  async function refreshState() {
    console.log('ℹ️ refreshState called - state synchronized via WebSocket only');
    return true;
  }

  // === GESTION VISIBILITÉ ===
  // Note: Plus besoin de polling au retour de focus - le WebSocket maintient l'état à jour
  function setupVisibilityListener() {
    const visibilityHandler = () => {
      if (!document.hidden) {
        console.log('ℹ️ App visible - state already synchronized via WebSocket');
      }
    };

    document.addEventListener('visibilitychange', visibilityHandler, { passive: true });
    window.addEventListener('focus', visibilityHandler, { passive: true });

    return () => {
      document.removeEventListener('visibilitychange', visibilityHandler);
      window.removeEventListener('focus', visibilityHandler);
    };
  }

  // === MISE À JOUR D'ÉTAT ===
  function updateSystemState(newState, source = 'unknown') {
    // Validation défensive des valeurs reçues du WebSocket
    // Note: Cette validation est une mesure de sécurité défensive côté frontend.
    // Elle ne devrait jamais être nécessaire si le backend fonctionne correctement,
    // mais protège contre les corruptions de données en transit.
    const validSources = ['none', 'librespot', 'bluetooth', 'roc', 'radio'];
    const validStates = ['inactive', 'ready', 'connected', 'error'];

    // Valider active_source
    const activeSource = validSources.includes(newState.active_source)
      ? newState.active_source
      : 'none';

    // Valider plugin_state
    const pluginState = validStates.includes(newState.plugin_state)
      ? newState.plugin_state
      : 'inactive';

    // Valider transitioning (doit être boolean)
    const transitioning = typeof newState.transitioning === 'boolean'
      ? newState.transitioning
      : false;

    // Valider target_source
    const targetSource = newState.target_source && validSources.includes(newState.target_source)
      ? newState.target_source
      : null;

    // Valider metadata (doit être objet)
    const metadata = newState.metadata && typeof newState.metadata === 'object' && !Array.isArray(newState.metadata)
      ? newState.metadata
      : {};

    // Valider multiroom_enabled et equalizer_enabled (doivent être boolean)
    const multiroomEnabled = typeof newState.multiroom_enabled === 'boolean'
      ? newState.multiroom_enabled
      : systemState.value.multiroom_enabled;

    const equalizerEnabled = typeof newState.equalizer_enabled === 'boolean'
      ? newState.equalizer_enabled
      : systemState.value.equalizer_enabled;

    systemState.value = {
      active_source: activeSource,
      plugin_state: pluginState,
      transitioning: transitioning,
      target_source: targetSource,
      metadata: metadata,
      error: newState.error || null,
      multiroom_enabled: multiroomEnabled,
      equalizer_enabled: equalizerEnabled
    };

    // Log warning si validation a modifié des valeurs
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
    if (event.data && typeof event.data.volume === 'number') {
      volumeState.value.currentVolume = event.data.volume;

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