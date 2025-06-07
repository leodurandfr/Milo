<template>
  <div class="home-view">
    <div class="status-panel">
      <h1>oakOS</h1>
      <h2>Source: {{ sourceLabel }}</h2>
      <div v-if="unifiedStore.error" class="error-message">
        {{ unifiedStore.error }}
      </div>
    </div>

    <MultiroomToggle />

    <div class="source-buttons">
      <button v-for="source in sources" :key="source.id" @click="changeSource(source.id)"
        :disabled="unifiedStore.isTransitioning || unifiedStore.currentSource === source.id"
        :class="['source-button', source.id]">
        {{ source.label }}
      </button>
    </div>

    <div v-if="unifiedStore.isTransitioning" class="transition-state">
      <h2>Chargement...</h2>
    </div>

    <component v-else-if="currentComponent" :is="currentComponent" />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import useWebSocket from '@/services/websocket';
import MultiroomToggle from '@/components/routing/MultiroomToggle.vue';
import LibrespotDisplay from '@/components/sources/librespot/LibrespotDisplay.vue';
import BluetoothDisplay from '@/components/sources/bluetooth/BluetoothDisplay.vue';
import RocDisplay from '@/components/sources/roc/RocDisplay.vue';

const unifiedStore = useUnifiedAudioStore();
const { on } = useWebSocket();

const sources = [
  { id: 'librespot', label: 'Spotify' },
  { id: 'bluetooth', label: 'Bluetooth' },
  { id: 'roc', label: 'ROC for Mac' },
  { id: 'webradio', label: 'Web Radio' }
];

const sourceLabel = computed(() => {
  const source = sources.find(s => s.id === unifiedStore.currentSource);
  return source ? source.label : 'Aucune';
});

const currentComponent = computed(() => {
  switch (unifiedStore.currentSource) {
    case 'librespot': return LibrespotDisplay;
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

  // OPTIM: Faire confiance uniquement au WebSocket, pas de fallback
  console.log('HomeView mounted, WebSocket should provide initial state');
});
</script>

<style scoped>
.home-view {
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
}

.status-panel {
  background: white;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
  text-align: center;
}

.status-panel h1 {
  margin: 0 0 10px 0;
  color: #333;
  font-size: 28px;
}

.status-panel h2 {
  margin: 0;
  color: #666;
  font-size: 18px;
  font-weight: normal;
}

.error-message {
  background: #ffe6e6;
  color: #d00;
  padding: 10px;
  border-radius: 4px;
  margin-top: 10px;
  font-size: 14px;
}

.source-buttons {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin: 20px 0;
}

.source-button {
  padding: 16px 20px;
  font-size: 16px;
  font-weight: bold;
  border: 2px solid #ddd;
  border-radius: 8px;
  background: white;
  cursor: pointer;
  transition: all 0.2s;
}

.source-button:hover:not(:disabled) {
  border-color: #2196F3;
  background: #f0f8ff;
}

.source-button:disabled {
  background: #f5f5f5;
  color: #999;
  cursor: not-allowed;
}

.source-button.librespot {
  border-color: #1DB954;
}

.source-button.bluetooth {
  border-color: #0082FC;
}

.source-button.roc {
  border-color: #FF6B35;
}

.source-button.webradio {
  border-color: #9C27B0;
}

.transition-state {
  text-align: center;
  padding: 40px;
  color: #666;
}

.transition-state h2 {
  margin: 0;
  font-size: 20px;
}
</style>