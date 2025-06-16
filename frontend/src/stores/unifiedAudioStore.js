// frontend/src/stores/unifiedAudioStore.js - Version refactorisée multiroom_enabled
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useUnifiedAudioStore = defineStore('unifiedAudio', () => {
  // État miroir du backend - Refactorisé avec multiroom_enabled
  const systemState = ref({
    active_source: 'none',
    plugin_state: 'inactive',
    transitioning: false,
    metadata: {},
    error: null,
    multiroom_enabled: false,   // Par défaut multiroom désactivé
    equalizer_enabled: false
  });
  
  // Getters unifiés - Refactorisés
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  const error = computed(() => systemState.value.error);
  const multiroomEnabled = computed(() => systemState.value.multiroom_enabled);  // Refactorisé
  const equalizerEnabled = computed(() => systemState.value.equalizer_enabled);
  
  // Actions unifiées
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
  
  function updateState(event) {
    if (event.data.full_state) {
      // Mise à jour complète de l'état - Version refactorisée
      const newState = event.data.full_state;
      
      systemState.value = {
        active_source: newState.active_source || 'none',
        plugin_state: newState.plugin_state || 'inactive',
        transitioning: newState.transitioning || false,
        metadata: newState.metadata || {},
        error: newState.error || null,
        multiroom_enabled: newState.multiroom_enabled !== undefined ? newState.multiroom_enabled : false,  // Par défaut False
        equalizer_enabled: newState.equalizer_enabled || false
      };
      
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
    multiroomEnabled,     // Refactorisé : plus de routingMode
    equalizerEnabled, 
    
    // Actions
    changeSource,
    sendCommand,
    setMultiroomEnabled,  // Refactorisé : plus de setRoutingMode
    setEqualizerEnabled,
    updateState
  };
});