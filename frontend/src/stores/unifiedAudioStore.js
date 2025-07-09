// frontend/src/stores/unifiedAudioStore.js - Version avec refresh global ciblé
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // État miroir du backend
  const systemState = ref({
    active_source: 'none',
    plugin_state: 'inactive',
    transitioning: false,
    metadata: {},
    error: null,
    multiroom_enabled: false,
    equalizer_enabled: false
  });
  
  // Source cible pendant la transition
  const transitionTarget = ref('none');
  
  // Getters unifiés
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  const error = computed(() => systemState.value.error);
  const multiroomEnabled = computed(() => systemState.value.multiroom_enabled);
  const equalizerEnabled = computed(() => systemState.value.equalizer_enabled);
  const transitionTargetSource = computed(() => transitionTarget.value);
  
  // Actions unifiées
  async function changeSource(source) {
    try {
      transitionTarget.value = source;
      const response = await axios.post(`/api/audio/source/${source}`);
      return response.data.status === 'success';
    } catch (err) {
      console.error('Change source error:', err);
      transitionTarget.value = systemState.value.active_source;
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
  
  // === REFRESH D'ÉTAT GLOBAL CIBLÉ ===
  
  async function refreshCurrentState() {
    try {
      console.log('🔄 Refreshing global audio state via fresh API...');
      
      // Si un plugin librespot est actif, utiliser l'endpoint fresh-status
      if (systemState.value.active_source === 'librespot') {
        console.log('🔄 Calling oakOS fresh-status API for librespot...');
        
        try {
          // Appeler l'endpoint oakOS qui interroge go-librespot
          const response = await axios.get('/librespot/fresh-status');
          
          if (response.data && response.data.status === 'success') {
            const freshData = response.data;
            console.log('📦 Fresh data from oakOS librespot API:', freshData);
            
            // Mettre à jour l'état système avec les métadonnées fraîches
            systemState.value.metadata = freshData.fresh_metadata || {};
            systemState.value.plugin_state = freshData.device_connected ? 'connected' : 'ready';
            
            console.log('✅ Fresh librespot metadata updated:', freshData.fresh_metadata);
            return true;
          } else {
            console.warn('⚠️ Fresh-status API error:', response.data?.message);
          }
        } catch (freshApiError) {
          console.warn('⚠️ Fresh-status API error, falling back to oakOS API:', freshApiError.message);
        }
      }
      
      // Fallback : utiliser l'API oakOS pour les autres sources ou en cas d'erreur
      console.log('🔄 Using oakOS API for state refresh...');
      const response = await axios.get('/api/audio/state');
      
      if (response.data) {
        const newState = response.data;
        
        systemState.value = {
          active_source: newState.active_source || 'none',
          plugin_state: newState.plugin_state || 'inactive',
          transitioning: newState.transitioning || false,
          metadata: newState.metadata || {},
          error: newState.error || null,
          multiroom_enabled: newState.multiroom_enabled !== undefined ? newState.multiroom_enabled : false,
          equalizer_enabled: newState.equalizer_enabled || false
        };
        
        if (!newState.transitioning) {
          transitionTarget.value = newState.active_source || 'none';
        }
        
        console.log('✅ oakOS state refreshed');
        return true;
      }
      
      return false;
      
    } catch (error) {
      console.error('❌ Error refreshing current state:', error);
      return false;
    }
  }
  
  async function refreshAllStates() {
    try {
      console.log('🔄 Refreshing all global states...');
      
      // 1. État audio principal
      await refreshCurrentState();
      
      // 2. Importer et refresh le volume store
      const { useVolumeStore } = await import('./volumeStore');
      const volumeStore = useVolumeStore();
      await volumeStore.getVolumeStatus();
      
      console.log('✅ All states refreshed successfully');
      return true;
      
    } catch (error) {
      console.error('❌ Error refreshing all states:', error);
      return false;
    }
  }
  
  // === GESTION VISIBILITÉ DE L'ONGLET ===
  
  let visibilityHandler = null;
  let isVisibilityListenerActive = false;
  
  function setupVisibilityListener() {
    if (isVisibilityListenerActive) return;
    
    visibilityHandler = async () => {
      if (!document.hidden) {
        // Onglet redevient actif - refresh global avec délai
        console.log('👁️ Tab became visible, refreshing states...');
        setTimeout(() => {
          refreshAllStates();
        }, 300);
      }
    };
    
    document.addEventListener('visibilitychange', visibilityHandler);
    window.addEventListener('focus', visibilityHandler);
    isVisibilityListenerActive = true;
    
    console.log('👁️ Global visibility listener setup');
  }
  
  function removeVisibilityListener() {
    if (visibilityHandler && isVisibilityListenerActive) {
      document.removeEventListener('visibilitychange', visibilityHandler);
      window.removeEventListener('focus', visibilityHandler);
      isVisibilityListenerActive = false;
      visibilityHandler = null;
      
      console.log('👁️ Global visibility listener removed');
    }
  }
  
  function updateState(event) {
    if (event.data.full_state) {
      const newState = event.data.full_state;
      
      systemState.value = {
        active_source: newState.active_source || 'none',
        plugin_state: newState.plugin_state || 'inactive',
        transitioning: newState.transitioning || false,
        metadata: newState.metadata || {},
        error: newState.error || null,
        multiroom_enabled: newState.multiroom_enabled !== undefined ? newState.multiroom_enabled : false,
        equalizer_enabled: newState.equalizer_enabled || false
      };
      
      // Réinitialiser transitionTarget quand la transition est terminée
      if (!newState.transitioning) {
        transitionTarget.value = newState.active_source || 'none';
      }
      
      // Log pour debug
      if (event.data.initial_connection) {
        console.log('Initial state received via WebSocket:', systemState.value);
      }
    }
  }
  
  return {
    // État
    systemState,
    
    // Getters
    currentSource,
    pluginState,
    isTransitioning,
    metadata,
    error,
    multiroomEnabled,
    equalizerEnabled,
    transitionTargetSource,
    
    // Actions
    changeSource,
    sendCommand,
    setMultiroomEnabled,
    setEqualizerEnabled,
    updateState,
    
    // Refresh global
    refreshCurrentState,
    refreshAllStates,
    setupVisibilityListener,
    removeVisibilityListener
  };
});