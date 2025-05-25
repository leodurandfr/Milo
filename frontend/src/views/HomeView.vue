<template>
  <div class="home-view">
    <div class="status-panel">
      <h1>oakOS</h1>
      <h2>Source: {{ sourceLabel }}</h2>
      <div v-if="unifiedStore.error" class="error-message">
        {{ unifiedStore.error }}
      </div>
    </div>

    <div class="source-buttons">
      <button 
        v-for="source in sources" 
        :key="source.id"
        @click="changeSource(source.id)"
        :disabled="unifiedStore.isTransitioning || unifiedStore.currentSource === source.id"
        :class="['source-button', source.id]"
      >
        {{ source.label }}
      </button>
    </div>

    <div v-if="unifiedStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>
    
    <component 
      v-else-if="currentComponent"
      :is="currentComponent"
    />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import SnapclientComponent from '@/components/sources/snapclient/SnapclientComponent.vue';
import BluetoothDisplay from '@/components/sources/bluetooth/BluetoothDisplay.vue';
import RocDisplay from '@/components/sources/roc/RocDisplay.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const sources = [
  { id: 'librespot', label: 'Spotify' },
  { id: 'bluetooth', label: 'Bluetooth' },
  { id: 'roc', label: 'ROC' },
  { id: 'snapclient', label: 'MacOS' },
  { id: 'webradio', label: 'Web Radio' }
];

const sourceLabel = computed(() => {
  const source = sources.find(s => s.id === unifiedStore.currentSource);
  return source ? source.label : 'Aucune';
});

const currentComponent = computed(() => {
  switch (unifiedStore.currentSource) {
    case 'librespot': return LibrespotDisplay;
    case 'snapclient': return SnapclientComponent;
    case 'bluetooth': return BluetoothDisplay;
    case 'roc': return RocDisplay;
    default: return null;
  }
});

function changeSource(source) {
  unifiedStore.changeSource(source);
}

onMounted(() => {
  // S'abonner à TOUS les événements qui affectent l'état
  on('system', 'state_changed', (event) => {
    console.log('Received state_changed:', event);
    unifiedStore.updateState(event);
  });
  
  on('system', 'transition_start', (event) => {
    console.log('Received transition_start:', event);
    unifiedStore.updateState(event);
  });
  
  on('system', 'transition_complete', (event) => {
    console.log('Received transition_complete:', event);
    unifiedStore.updateState(event);
  });
  
  on('system', 'error', (event) => {
    console.log('Received system error:', event);
    unifiedStore.updateState(event);
  });
  
  on('plugin', 'plugin_state_changed', (event) => {
    console.log('Received plugin_state_changed:', event);
    unifiedStore.updateState(event);
  });
  
  on('plugin', 'plugin_metadata', (event) => {
    console.log('Received plugin_metadata:', event);
    unifiedStore.updateState(event);
  });
});
</script>