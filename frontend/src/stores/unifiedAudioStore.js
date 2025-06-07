// frontend/src/stores/unifiedAudioStore.js
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
    routing_mode: 'multiroom'
  });
  
  // Getters unifiés
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  const error = computed(() => systemState.value.error);
  const routingMode = computed(() => systemState.value.routing_mode);
  
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
  
  async function setRoutingMode(mode) {
    try {
      const response = await axios.post(`/api/routing/mode/${mode}`);
      return response.data.status === 'success';
    } catch (err) {
      console.error('Set routing mode error:', err);
      return false;
    }
  }
  
  function updateState(event) {
    if (event.data.full_state) {
      // Mise à jour complète de l'état
      const newState = event.data.full_state;
      
      // S'assurer que tous les champs sont présents
      systemState.value = {
        active_source: newState.active_source || 'none',
        plugin_state: newState.plugin_state || 'inactive',
        transitioning: newState.transitioning || false,
        metadata: newState.metadata || {},
        error: newState.error || null,
        routing_mode: newState.routing_mode || 'multiroom'
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
    routingMode,
    
    // Actions
    changeSource,
    sendCommand,
    setRoutingMode,
    updateState
  };
});