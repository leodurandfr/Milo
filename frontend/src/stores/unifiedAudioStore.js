// frontend/src/stores/unifiedAudioStore.js - Version avec transitionTargetSource
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
  
  // AJOUT : Source cible pendant la transition
  const transitionTarget = ref('none');
  
  // Getters unifiés
  const currentSource = computed(() => systemState.value.active_source);
  const pluginState = computed(() => systemState.value.plugin_state);
  const isTransitioning = computed(() => systemState.value.transitioning);
  const metadata = computed(() => systemState.value.metadata || {});
  const error = computed(() => systemState.value.error);
  const multiroomEnabled = computed(() => systemState.value.multiroom_enabled);
  const equalizerEnabled = computed(() => systemState.value.equalizer_enabled);
  
  // AJOUT : Getter pour la source cible
  const transitionTargetSource = computed(() => transitionTarget.value);
  
  // Actions unifiées
  async function changeSource(source) {
    try {
      // AJOUT : Stocker la source cible
      transitionTarget.value = source;
      
      const response = await axios.post(`/api/audio/source/${source}`);
      return response.data.status === 'success';
    } catch (err) {
      console.error('Change source error:', err);
      // AJOUT : Reset en cas d'erreur
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
      
      // AJOUT : Réinitialiser transitionTarget quand la transition est terminée
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
    transitionTargetSource, // AJOUT
    
    // Actions
    changeSource,
    sendCommand,
    setMultiroomEnabled,
    setEqualizerEnabled,
    updateState
  };
});