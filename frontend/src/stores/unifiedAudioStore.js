// unifiedAudioStore.js - Version finale OPTIM
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
  
  const volumeState = ref({
    currentVolume: 0,
    limits: { min: 0, max: 100 }
  });
  
  let volumeBarRef = null;
  
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
  
  const currentVolume = computed(() => volumeState.value.currentVolume);
  const volumeLimits = computed(() => volumeState.value.limits);
  
  // === ACTIONS AUDIO ===
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
  
  // === REFRESH ===
  async function refreshState() {
    try {
      console.log('🔄 Refreshing unified state...');
      
      // État audio principal
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
      
      // État volume
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
  
  // === GESTION VISIBILITÉ ===
  function setupVisibilityListener() {
    const visibilityHandler = () => {
      if (!document.hidden) {
        // Délai pour laisser le WebSocket se reconnecter
        setTimeout(refreshState, 500);
      }
    };
    
    document.addEventListener('visibilitychange', visibilityHandler);
    window.addEventListener('focus', visibilityHandler);
    
    return () => {
      document.removeEventListener('visibilitychange', visibilityHandler);
      window.removeEventListener('focus', visibilityHandler);
    };
  }
  
  // === MISE À JOUR D'ÉTAT ===
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
  
  function handleVolumeEvent(event) {
    if (event.data && typeof event.data.volume === 'number') {
      volumeState.value.currentVolume = event.data.volume;
      
      if (event.data.limits) {
        volumeState.value.limits = event.data.limits;
      }
      
      if (event.data.show_bar && volumeBarRef && volumeBarRef.value) {
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
    volumeLimits,
    
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