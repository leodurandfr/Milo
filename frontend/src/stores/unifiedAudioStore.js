// unifiedAudioStore.js - Correction pour Volume Bar
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // === ÉTAT UNIQUE (source de vérité du backend) ===
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
  
  // === ÉTAT VOLUME INTÉGRÉ ===
  const volumeState = ref({
    currentVolume: 0,
    isAdjusting: false,
    limits: { min: 0, max: 100 }
  });
  
  // === RÉFÉRENCE VOLUMEBAR (CORRECTION timing) ===
  let volumeBarRef = null;
  
  // === GETTERS AUDIO SIMPLIFIÉS ===
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  const error = computed(() => systemState.value.error);
  const multiroomEnabled = computed(() => systemState.value.multiroom_enabled);
  const equalizerEnabled = computed(() => systemState.value.equalizer_enabled);
  
  // SIMPLIFIÉ : Une seule source affichée (utilise target_source du backend)
  const displayedSource = computed(() => {
    if (systemState.value.transitioning && systemState.value.target_source) {
      return systemState.value.target_source;
    }
    return systemState.value.active_source;
  });
  
  // === GETTERS VOLUME ===
  const currentVolume = computed(() => volumeState.value.currentVolume);
  const isAdjustingVolume = computed(() => volumeState.value.isAdjusting);
  const volumeLimits = computed(() => volumeState.value.limits);
  
  // === ACTIONS AUDIO (simplifiées) ===
  async function changeSource(source) {
    try {
      const response = await axios.post(`/api/audio/source/${source}`);
      return response.data.status === 'success';
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
    if (volumeState.value.isAdjusting) return false;
    
    try {
      volumeState.value.isAdjusting = true;
      
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
    } finally {
      setTimeout(() => {
        volumeState.value.isAdjusting = false;
      }, 100);
    }
  }
  
  async function adjustVolume(delta, showBar = true) {
    if (volumeState.value.isAdjusting) return false;
    
    try {
      // 1. Feedback immédiat local
      volumeState.value.currentVolume = Math.max(0, Math.min(100, volumeState.value.currentVolume + delta));
      
      // 2. Afficher la barre immédiatement si demandé
      if (showBar && volumeBarRef?.value?.showVolume) {
        volumeBarRef.value.showVolume();
      }
      
      // 3. Envoyer la requête (sans attendre la réponse)
      fetch('/api/volume/adjust', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delta, show_bar: false }) // Pas de barre via WebSocket
      }).catch(error => {
        console.error('Erreur volume:', error);
        // En cas d'erreur, on pourrait refresh le volume réel
      });
      
      return true;
      
    } catch (error) {
      console.error('Erreur ajustement volume:', error);
      return false;
    }
  }
  
  async function increaseVolume() {
    return await adjustVolume(5);
  }
  
  async function decreaseVolume() {
    return await adjustVolume(-5);
  }
  
  // === REFRESH SIMPLIFIÉ ===
  async function refreshState() {
    try {
      console.log('🔄 Refreshing unified state...');
      
      // 1. État audio principal
      if (systemState.value.active_source === 'librespot') {
        try {
          const response = await axios.get('/librespot/fresh-status');
          if (response.data?.status === 'success') {
            systemState.value.metadata = response.data.fresh_metadata || {};
            systemState.value.plugin_state = response.data.device_connected ? 'connected' : 'ready';
            console.log('✅ Fresh librespot data updated');
          }
        } catch (freshApiError) {
          console.warn('⚠️ Fresh-status fallback to main API');
        }
      }
      
      // Fallback API audio
      const audioResponse = await axios.get('/api/audio/state');
      if (audioResponse.data) {
        updateSystemState(audioResponse.data);
      }
      
      // 2. État volume
      const volumeResponse = await axios.get('/api/volume/status');
      if (volumeResponse.data?.status === 'success') {
        const data = volumeResponse.data.data;
        volumeState.value.currentVolume = data.volume || 0;
        if (data.limits) {
          volumeState.value.limits = data.limits;
        }
      }
      
      console.log('✅ Unified state refreshed');
      return true;
      
    } catch (error) {
      console.error('❌ Error refreshing state:', error);
      return false;
    }
  }
  
  // === GESTION VISIBILITÉ SIMPLIFIÉE ===
  let visibilityHandler = null;
  
  function setupVisibilityListener() {
    if (visibilityHandler) return;
    
    visibilityHandler = async () => {
      if (!document.hidden) {
        console.log('👁️ Tab visible, refreshing...');
        setTimeout(refreshState, 300);
      }
    };
    
    document.addEventListener('visibilitychange', visibilityHandler);
    window.addEventListener('focus', visibilityHandler);
  }
  
  function removeVisibilityListener() {
    if (visibilityHandler) {
      document.removeEventListener('visibilitychange', visibilityHandler);
      window.removeEventListener('focus', visibilityHandler);
      visibilityHandler = null;
    }
  }
  
  // === MISE À JOUR D'ÉTAT SIMPLIFIÉE ===
  function updateSystemState(newState) {
    systemState.value = {
      active_source: newState.active_source || 'none',
      plugin_state: newState.plugin_state || 'inactive',
      transitioning: newState.transitioning || false,
      target_source: newState.target_source || null,
      metadata: newState.metadata || {},
      error: newState.error || null,
      multiroom_enabled: newState.multiroom_enabled !== undefined ? newState.multiroom_enabled : false,
      equalizer_enabled: newState.equalizer_enabled || false
    };
  }
  
  function updateState(event) {
    if (event.data?.full_state) {
      updateSystemState(event.data.full_state);
    }
  }
  
  // === GESTION ÉVÉNEMENTS VOLUME (CORRECTION timing) ===
  function handleVolumeEvent(event) {
    if (event.data && typeof event.data.volume === 'number') {
      volumeState.value.currentVolume = event.data.volume;
      
      if (event.data.limits) {
        volumeState.value.limits = event.data.limits;
      }
      
      // ✅ CORRECTION : Accès à la ref reactive comme dans l'ancien store
      if (event.data.show_bar && volumeBarRef && volumeBarRef.value) {
        try {
          volumeBarRef.value.showVolume();
        } catch (error) {
          console.warn('Failed to show volume bar:', error);
        }
      }
    }
  }
  
  // === GESTION RÉFÉRENCE VOLUMEBAR (CORRECTION timing) ===
  function setVolumeBarRef(reactiveRef) {
    console.log('🎚️ Setting VolumeBar reactive ref:', reactiveRef);
    volumeBarRef = reactiveRef; // Stocker la ref reactive, pas sa valeur
  }
  
  return {
    // === ÉTAT ===
    systemState,
    volumeState,
    
    // === GETTERS AUDIO ===
    currentSource,
    pluginState,
    isTransitioning,
    metadata,
    error,
    multiroomEnabled,
    equalizerEnabled,
    displayedSource,
    
    // === GETTERS VOLUME ===
    currentVolume,
    isAdjustingVolume,
    volumeLimits,
    
    // === ACTIONS AUDIO ===
    changeSource,
    sendCommand,
    setMultiroomEnabled,
    setEqualizerEnabled,
    updateState,
    
    // === ACTIONS VOLUME ===
    setVolume,
    adjustVolume,
    increaseVolume,
    decreaseVolume,
    handleVolumeEvent,
    setVolumeBarRef,
    
    // === REFRESH ET VISIBILITÉ ===
    refreshState,
    setupVisibilityListener,
    removeVisibilityListener
  };
});